"""Test live feed and billing endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Activity, Base, Business


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    from arclane.api.routes import cycles as cycles_module
    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, session_factory

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def auth_client():
    """Authenticated test client with JWT header."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    from arclane.api.routes import cycles as cycles_module
    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            reg = await c.post("/api/auth/register", json={
                "email": "test@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})
            yield c, session_factory

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_live_feed_empty(client):
    c, _ = client
    resp = await c.get("/api/live")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_live_stats_empty(client):
    c, _ = client
    resp = await c.get("/api/live/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["businesses"] == 0
    assert data["cycles_completed"] == 0


async def test_live_feed_with_activity(client):
    c, session_factory = client

    async with session_factory() as session:
        biz = Business(
            slug="live-test", name="Live Test",
            description="Testing live feed", owner_email="test@example.com",
        )
        session.add(biz)
        await session.commit()

        activity = Activity(
            business_id=biz.id, agent="cmo",
            action="Created blog post", detail="Test post",
        )
        session.add(activity)
        await session.commit()

    resp = await c.get("/api/live")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["action"] == "Created blog post"
    assert data[0]["business_name"] == "Live Test"
    assert data[0]["business_slug"] == "live-test"


async def test_live_stats_with_data(client):
    c, session_factory = client

    async with session_factory() as session:
        biz = Business(
            slug="stats-test", name="Stats Test",
            description="Testing stats", owner_email="test@example.com",
        )
        session.add(biz)
        await session.commit()

    resp = await c.get("/api/live/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["businesses"] == 1


async def test_live_feed_redacts_identity_in_production(client):
    c, session_factory = client

    async with session_factory() as session:
        biz = Business(
            slug="private-test", name="Private Test",
            description="Testing production redaction", owner_email="test@example.com",
        )
        session.add(biz)
        await session.commit()

        activity = Activity(
            business_id=biz.id, agent="ops",
            action="Provisioning complete", detail="Workspace deployed on internal port 9012.",
        )
        session.add(activity)
        await session.commit()

    from arclane.core.config import settings
    original_env = settings.env
    original_identity = settings.public_live_feed_identity
    original_detail = settings.public_live_feed_detail
    settings.env = "production"
    settings.public_live_feed_identity = False
    settings.public_live_feed_detail = False
    try:
        resp = await c.get("/api/live")
    finally:
        settings.env = original_env
        settings.public_live_feed_identity = original_identity
        settings.public_live_feed_detail = original_detail

    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["business_name"] == "Arclane tenant"
    assert data[0]["business_slug"] == ""
    assert data[0]["detail"] is None


async def test_billing_status(auth_client):
    c, _ = auth_client

    await c.post("/api/businesses", json={
        "name": "Billing Test",
        "description": "Testing billing",
        "owner_email": "test@example.com",
    })

    resp = await c.get("/api/businesses/billing-test/billing/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan"] == "preview"
    assert data["active"] is True


async def test_billing_webhook_subscription_created(auth_client):
    c, _ = auth_client

    await c.post("/api/businesses", json={
        "name": "Webhook Test",
        "description": "Testing webhooks",
        "owner_email": "webhook@example.com",
    })

    from arclane.core.config import settings
    original = settings.zuul_service_token
    settings.zuul_service_token = "test-webhook-secret"
    try:
        resp = await c.post(
            "/api/businesses/webhook-test/billing/webhook",
            json={
                "event": "subscription.created",
                "license_key": "ARC-XXXXX-XXXXX",
                "customer_email": "webhook@example.com",
                "plan": "pro",
                "business_slug": "webhook-test",
            },
            headers={"X-Service-Token": "test-webhook-secret"},
        )
    finally:
        settings.zuul_service_token = original
    assert resp.status_code == 200

    # Verify plan was updated
    resp = await c.get("/api/businesses/webhook-test/billing/status")
    data = resp.json()
    assert data["plan"] == "pro"
    assert data["credits_remaining"] == 20


async def test_billing_webhook_invalid_token(auth_client):
    c, _ = auth_client

    await c.post("/api/businesses", json={
        "name": "Token Test",
        "description": "Testing token",
        "owner_email": "test@example.com",
    })

    # Set a real token so the empty one fails
    from arclane.core.config import settings
    original = settings.zuul_service_token
    settings.zuul_service_token = "real-secret"

    resp = await c.post(
        "/api/businesses/token-test/billing/webhook",
        json={
            "event": "subscription.created",
            "customer_email": "test@example.com",
            "plan": "pro",
        },
        headers={"X-Service-Token": "wrong-token"},
    )
    assert resp.status_code == 403

    settings.zuul_service_token = original
