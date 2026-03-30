"""Tests for scheduled content publishing and WebSocket broadcast wiring."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business, Content


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _create_business(factory, slug="pub-biz"):
    async with factory() as session:
        biz = Business(
            slug=slug, name="Pub Biz", description="Test",
            owner_email="test@test.com", plan="pro",
            working_days_remaining=20, working_days_bonus=0,
        )
        session.add(biz)
        await session.commit()
        return biz.id


# --- Scheduled content publishing ---


async def test_publish_due_content(db):
    """Content with scheduled status and past published_at gets published."""
    from arclane.engine.scheduler import _publish_scheduled_content

    biz_id = await _create_business(db)

    async with db() as session:
        # Due for publishing (scheduled time in the past)
        session.add(Content(
            business_id=biz_id, content_type="blog", title="Due Post",
            body="Content", status="scheduled",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ))
        # Not yet due (scheduled time in the future)
        session.add(Content(
            business_id=biz_id, content_type="blog", title="Future Post",
            body="Content", status="scheduled",
            published_at=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
        # Already published
        session.add(Content(
            business_id=biz_id, content_type="blog", title="Already Published",
            body="Content", status="published",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ))
        await session.commit()

    with patch("arclane.engine.scheduler.async_session", db):
        await _publish_scheduled_content()

    async with db() as session:
        result = await session.execute(select(Content).order_by(Content.id))
        items = result.scalars().all()
        assert items[0].title == "Due Post"
        assert items[0].status == "published"
        assert items[1].title == "Future Post"
        assert items[1].status == "scheduled"  # untouched
        assert items[2].title == "Already Published"
        assert items[2].status == "published"  # untouched


async def test_publish_noop_when_nothing_scheduled(db):
    """Publishing job is a no-op when no content is scheduled."""
    from arclane.engine.scheduler import _publish_scheduled_content

    with patch("arclane.engine.scheduler.async_session", db):
        await _publish_scheduled_content()  # should not raise


async def test_publish_ignores_draft_content(db):
    """Draft content with a published_at is not auto-published."""
    from arclane.engine.scheduler import _publish_scheduled_content

    biz_id = await _create_business(db)

    async with db() as session:
        session.add(Content(
            business_id=biz_id, content_type="social", title="Draft",
            body="Content", status="draft",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ))
        await session.commit()

    with patch("arclane.engine.scheduler.async_session", db):
        await _publish_scheduled_content()

    async with db() as session:
        result = await session.execute(select(Content))
        item = result.scalar_one()
        assert item.status == "draft"


# --- WebSocket broadcast wiring ---


async def test_websocket_manager_broadcast():
    """WebSocket manager broadcast sends to subscribed clients."""
    from arclane.performance.websocket import WebSocketManager

    mgr = WebSocketManager()

    mock_ws = AsyncMock()
    mock_ws.send_json = AsyncMock()

    await mgr.connect(mock_ws, "client-1", business_id=42)

    sent = await mgr.broadcast_activity(42, "Creating content", "cmo")
    assert sent >= 1
    mock_ws.send_json.assert_called()
    msg = mock_ws.send_json.call_args[0][0]
    assert msg["type"] == "activity"
    assert msg["business_id"] == 42
    assert msg["action"] == "Creating content"


async def test_websocket_manager_cycle_progress():
    """Cycle progress broadcasts to business subscribers."""
    from arclane.performance.websocket import WebSocketManager

    mgr = WebSocketManager()

    mock_ws = AsyncMock()
    mock_ws.send_json = AsyncMock()

    await mgr.connect(mock_ws, "client-1", business_id=7)

    sent = await mgr.broadcast_cycle_progress(7, 99, "running", 50.0)
    assert sent >= 1
    msg = mock_ws.send_json.call_args[0][0]
    assert msg["type"] == "cycle_progress"
    assert msg["progress_pct"] == 50.0


async def test_websocket_manager_no_subscribers():
    """Broadcast to empty channel returns 0."""
    from arclane.performance.websocket import WebSocketManager

    mgr = WebSocketManager()
    sent = await mgr.broadcast_activity(999, "test", "test")
    assert sent == 0


async def test_websocket_heartbeat_removes_dead():
    """Heartbeat removes clients that fail to receive pings."""
    import asyncio
    from arclane.performance.websocket import WebSocketManager

    mgr = WebSocketManager()

    # Client that raises on send
    dead_ws = AsyncMock()
    dead_ws.send_json = AsyncMock(side_effect=Exception("broken"))
    await mgr.connect(dead_ws, "dead-client")

    assert mgr.connection_count == 1

    # Run one heartbeat cycle with very short interval
    task = asyncio.create_task(mgr.heartbeat(interval_seconds=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert mgr.connection_count == 0


async def test_websocket_subscribe_unsubscribe():
    """Client can subscribe and unsubscribe from channels."""
    from arclane.performance.websocket import WebSocketManager
    import json

    mgr = WebSocketManager()
    mock_ws = AsyncMock()
    mock_ws.send_json = AsyncMock()

    await mgr.connect(mock_ws, "sub-test")

    # Subscribe
    resp = await mgr.handle_client_message(
        "sub-test",
        json.dumps({"type": "subscribe", "channel": "business:1"}),
        allowed_channels={"business:1"},
    )
    assert resp["type"] == "subscribed"

    # Verify broadcast reaches after subscribe
    sent = await mgr.broadcast("business:1", {"test": True})
    assert sent == 1

    # Unsubscribe
    resp = await mgr.handle_client_message(
        "sub-test",
        json.dumps({"type": "unsubscribe", "channel": "business:1"}),
        allowed_channels={"business:1"},
    )
    assert resp["type"] == "unsubscribed"

    # Verify broadcast no longer reaches
    mock_ws.send_json.reset_mock()
    sent = await mgr.broadcast("business:1", {"test": True})
    assert sent == 0


async def test_websocket_ping_pong():
    """Client ping gets a pong response."""
    from arclane.performance.websocket import WebSocketManager
    import json

    mgr = WebSocketManager()
    mock_ws = AsyncMock()
    await mgr.connect(mock_ws, "ping-test")

    resp = await mgr.handle_client_message(
        "ping-test",
        json.dumps({"type": "ping"}),
    )
    assert resp["type"] == "pong"
    assert "timestamp" in resp
