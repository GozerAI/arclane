"""Tests for operational routes — metrics, webhooks, CDN headers, time budgets."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base, Business, Cycle


@pytest.fixture
async def db_and_client():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield factory, client
    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_business(factory, slug="test-biz", plan="pro", working_days=20):
    async with factory() as session:
        biz = Business(
            slug=slug, name="Test Biz", description="Test",
            owner_email="test@test.com", plan=plan,
            working_days_remaining=working_days, working_days_bonus=0,
        )
        session.add(biz)
        await session.commit()
        return biz.id


def _auth_headers():
    import jwt
    from arclane.core.config import settings
    token = jwt.encode(
        {"sub": "test@test.com", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.secret_key, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# --- Prometheus metrics endpoint ---


async def test_prometheus_metrics(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


async def test_prometheus_metrics_after_cycle():
    """Pipeline metrics record cycle events correctly."""
    from arclane.performance.pipeline_metrics import PipelineMetrics

    pm = PipelineMetrics()
    pm.record_cycle_start("nightly", "pro")
    assert pm.active_cycles.get() == 1
    assert pm.cycles_started.get(labels={"trigger": "nightly", "plan": "pro"}) == 1

    pm.record_cycle_complete("nightly", "pro", 5.0, 3)
    assert pm.active_cycles.get() == 0
    assert pm.cycles_completed.get(labels={"trigger": "nightly", "plan": "pro"}) == 1
    assert pm.tasks_processed.get(labels={"trigger": "nightly"}) == 3

    prom = pm.to_prometheus()
    assert "arclane_cycles_started_total" in prom
    assert "arclane_cycles_completed_total" in prom


async def test_prometheus_failure_metrics():
    from arclane.performance.pipeline_metrics import PipelineMetrics

    pm = PipelineMetrics()
    pm.record_cycle_start("on_demand", "starter")
    pm.record_cycle_failure("on_demand", "starter")
    assert pm.cycles_failed.get(labels={"trigger": "on_demand", "plan": "starter"}) == 1
    assert pm.active_cycles.get() == 0


# --- Time budget stats ---


async def test_time_budget_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/time-budgets")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_violations" in data


async def test_time_budget_enforcement():
    """Time budget registry tracks violations."""
    from arclane.performance.time_budgets import TimeBudgetRegistry

    reg = TimeBudgetRegistry()
    assert reg.check_budget("/health", 30)  # within 50ms budget
    assert not reg.check_budget("/health", 100)  # exceeds 50ms budget
    stats = reg.stats()
    assert stats["total_violations"] == 1
    assert stats["worst_offenders"][0]["path"] == "/health"


async def test_time_budget_headers(db_and_client):
    """Time budget middleware adds timing headers."""
    _, client = db_and_client
    resp = await client.get("/api/ops/metrics")
    # The middleware should add timing headers to API requests
    assert "x-response-time-ms" in resp.headers
    assert "x-time-budget-ms" in resp.headers


# --- CDN cache headers ---


async def test_cdn_static_cache_headers(db_and_client):
    """Static assets get long cache headers."""
    _, client = db_and_client
    resp = await client.get("/static/app.js")
    if resp.status_code == 200:
        cache_control = resp.headers.get("cache-control", "")
        assert "public" in cache_control
        assert "immutable" in cache_control


async def test_cdn_api_no_cache(db_and_client):
    """API endpoints without cache rules get no-store."""
    _, client = db_and_client
    resp = await client.get("/api/ops/time-budgets")
    cache_control = resp.headers.get("cache-control", "")
    assert "no-store" in cache_control or "no-cache" in cache_control


async def test_cdn_health_cacheable(db_and_client):
    """Health endpoint is cacheable for 10 seconds."""
    _, client = db_and_client
    resp = await client.get("/health")
    cache_control = resp.headers.get("cache-control", "")
    assert "max-age=10" in cache_control


# --- WebSocket stats ---


async def test_ws_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/ws-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "connections" in data


# --- Webhook management ---


async def test_webhook_register(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    resp = await client.post(
        "/api/businesses/test-biz/ops/webhooks",
        json={"url": "https://hooks.example.com/arclane", "secret": "s3cret"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["registered"] is True
    assert data["url"] == "https://hooks.example.com/arclane"


async def test_webhook_get(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    # Register first
    await client.post(
        "/api/businesses/test-biz/ops/webhooks",
        json={"url": "https://hooks.example.com/test"},
        headers=_auth_headers(),
    )
    resp = await client.get(
        "/api/businesses/test-biz/ops/webhooks",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://hooks.example.com/test"


async def test_webhook_delete(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    await client.post(
        "/api/businesses/test-biz/ops/webhooks",
        json={"url": "https://hooks.example.com/del"},
        headers=_auth_headers(),
    )
    resp = await client.delete(
        "/api/businesses/test-biz/ops/webhooks",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200

    # Should be gone
    resp = await client.get(
        "/api/businesses/test-biz/ops/webhooks",
        headers=_auth_headers(),
    )
    assert resp.json() is None


async def test_webhook_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/webhooks/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "registered_webhooks" in data
    assert "total_deliveries" in data


async def test_webhook_notifier_delivery():
    """Webhook notifier delivers signed payloads with retry."""
    from arclane.performance.webhook_cycles import CycleWebhookNotifier, WebhookConfig

    notifier = CycleWebhookNotifier()

    # No webhook registered → returns None
    result = await notifier.notify_cycle_complete(999, 1, "completed")
    assert result is None

    # Register a webhook (will fail since URL is fake, but tests retry logic)
    notifier.register_webhook(1, WebhookConfig(
        url="http://localhost:1/nonexistent",
        secret="test-secret",
        retry_count=1,
        retry_delay_s=0.01,
    ))
    delivery = await notifier.notify_cycle_complete(1, 42, "completed", {"total": 3})
    assert delivery is not None
    assert delivery.attempts == 1
    assert delivery.success is False  # localhost:1 doesn't exist
    assert delivery.event == "cycle.completed"

    stats = notifier.stats()
    assert stats["total_deliveries"] == 1
    assert stats["failed"] == 1
