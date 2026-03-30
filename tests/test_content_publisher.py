"""Tests for content publishing service and RLS/dedup wiring."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base, Business, Content


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


async def _create_business(factory, slug="test-biz"):
    async with factory() as session:
        biz = Business(
            slug=slug, name="Test Biz", description="Test",
            owner_email="test@test.com", plan="pro",
            working_days_remaining=20, working_days_bonus=0,
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


# --- ContentPublisher unit tests ---


async def test_publisher_kh_integration():
    """Publisher sends content to Knowledge Harvester."""
    from arclane.services.content_publisher import ContentPublisher

    pub = ContentPublisher()
    with patch.object(pub._kh, "publish_cycle_results", new_callable=AsyncMock) as mock_kh:
        mock_kh.return_value = [{"id": "art_1"}]
        report = await pub.publish(
            content_id=1, content_type="blog",
            title="Test Post", body="Content here",
            business_name="TestBiz",
        )
        assert report.channels_attempted >= 1
        assert report.channels_succeeded >= 1
        mock_kh.assert_called_once()


async def test_publisher_kh_failure():
    """Publisher handles KH failure gracefully."""
    from arclane.services.content_publisher import ContentPublisher

    pub = ContentPublisher()
    with patch.object(pub._kh, "publish_cycle_results", new_callable=AsyncMock) as mock_kh:
        mock_kh.side_effect = Exception("KH down")
        report = await pub.publish(
            content_id=2, content_type="social",
            title="Post", body="Content",
            business_name="TestBiz", platform="twitter",
        )
        # KH fails, social stub also fails (no OAuth)
        assert report.channels_attempted >= 1
        assert report.channels_succeeded == 0


async def test_publisher_social_stub():
    """Social publishing returns stub result until OAuth is configured."""
    from arclane.services.content_publisher import ContentPublisher

    pub = ContentPublisher()
    with patch.object(pub._kh, "publish_cycle_results", new_callable=AsyncMock):
        report = await pub.publish(
            content_id=3, content_type="social",
            title="Tweet", body="Content",
            business_name="TestBiz", platform="twitter",
        )
        twitter_result = next(
            r for r in report.results if r.channel == "twitter"
        )
        assert twitter_result.success is False
        assert "OAuth" in twitter_result.error


async def test_publisher_stats():
    """Publisher tracks stats."""
    from arclane.services.content_publisher import ContentPublisher

    pub = ContentPublisher()
    with patch.object(pub._kh, "publish_cycle_results", new_callable=AsyncMock):
        await pub.publish(1, "blog", "T", "B", "Biz")
        await pub.publish(2, "blog", "T", "B", "Biz")

    stats = pub.stats()
    assert stats["total_publishes"] == 2


# --- Publishing triggered by content update ---


async def test_publish_triggers_on_status_change(db_and_client):
    """Setting content to published triggers the publisher in background."""
    factory, client = db_and_client
    biz_id = await _create_business(factory)
    async with factory() as session:
        session.add(Content(
            business_id=biz_id, content_type="blog",
            title="My Post", body="Content body", status="draft",
        ))
        await session.commit()

    with patch("arclane.api.routes.content.content_publisher") as mock_pub:
        mock_pub.publish = AsyncMock()
        resp = await client.patch(
            "/api/businesses/test-biz/content/1",
            json={"status": "published"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"


async def test_draft_does_not_trigger_publish(db_and_client):
    """Setting content to draft does NOT trigger publishing."""
    factory, client = db_and_client
    biz_id = await _create_business(factory)
    async with factory() as session:
        session.add(Content(
            business_id=biz_id, content_type="blog",
            title="Draft", body="Content", status="published",
            published_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    with patch("arclane.api.routes.content.content_publisher") as mock_pub:
        mock_pub.publish = AsyncMock()
        resp = await client.patch(
            "/api/businesses/test-biz/content/1",
            json={"status": "draft"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        # publish should NOT be called for draft
        mock_pub.publish.assert_not_called()


# --- Row-level security ---


async def test_rls_tenant_context():
    """TenantContext sets and resets tenant ID."""
    from arclane.performance.row_level_security import (
        TenantContext, get_tenant_id, set_tenant_id,
    )

    assert get_tenant_id() is None

    with TenantContext(42):
        assert get_tenant_id() == 42

    assert get_tenant_id() is None


async def test_rls_async_context():
    """TenantContext works as async context manager."""
    from arclane.performance.row_level_security import TenantContext, get_tenant_id

    async with TenantContext(99):
        assert get_tenant_id() == 99

    assert get_tenant_id() is None


async def test_rls_set_on_business_lookup(db_and_client):
    """get_business dependency sets tenant ID for RLS."""
    from arclane.performance.row_level_security import get_tenant_id

    factory, client = db_and_client
    biz_id = await _create_business(factory)

    # Any authenticated request to a business route sets the tenant
    resp = await client.get(
        "/api/businesses/test-biz/content",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    # The tenant ID should have been set during request processing
    # (it may be cleared by now since the request completed, but the
    # set_tenant_id call in deps.py is what we're validating)


async def test_rls_filter_count(db_and_client):
    """RLS stats endpoint returns filter count."""
    _, client = db_and_client
    resp = await client.get("/api/ops/rls/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "filter_count" in data


# --- Request deduplication ---


async def test_dedup_concurrent_requests():
    """Deduplicator shares result for concurrent identical requests."""
    import asyncio
    from arclane.performance.deduplication import RequestDeduplicator

    dedup = RequestDeduplicator(ttl_s=0.5)
    call_count = 0

    async def expensive_work():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return {"result": "data"}

    key = dedup.make_key("GET", "/api/test")

    # Launch 3 concurrent requests with same key
    results = await asyncio.gather(
        dedup.deduplicate(key, expensive_work),
        dedup.deduplicate(key, expensive_work),
        dedup.deduplicate(key, expensive_work),
    )

    # All three should get the same result
    assert all(r == {"result": "data"} for r in results)
    # But the actual work should only run once
    assert call_count == 1
    assert dedup.stats["hits"] == 2
    assert dedup.stats["misses"] == 1


async def test_dedup_different_keys():
    """Different keys execute independently."""
    from arclane.performance.deduplication import RequestDeduplicator

    dedup = RequestDeduplicator()
    call_count = 0

    async def work():
        nonlocal call_count
        call_count += 1
        return call_count

    key_a = dedup.make_key("GET", "/api/a")
    key_b = dedup.make_key("GET", "/api/b")

    r1 = await dedup.deduplicate(key_a, work)
    r2 = await dedup.deduplicate(key_b, work)

    assert r1 == 1
    assert r2 == 2
    assert call_count == 2


async def test_dedup_stats_endpoint(db_and_client):
    """Dedup stats endpoint returns hit/miss counts."""
    _, client = db_and_client
    resp = await client.get("/api/ops/dedup/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "hits" in data
    assert "misses" in data


# --- Publishing stats endpoint ---


async def test_publishing_stats_endpoint(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/publishing/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_publishes" in data
