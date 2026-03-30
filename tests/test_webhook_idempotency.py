"""Tests for billing webhook idempotency."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Activity, Base, Business


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _create_business(factory, slug="idempotent-biz", plan="preview", working_days=3):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.title(),
            description="Test",
            owner_email="test@test.com",
            plan=plan,
            working_days_remaining=working_days,
            working_days_bonus=0,
        )
        session.add(biz)
        await session.commit()
        return biz.id


async def _process_webhook(db, payload_dict):
    """Simulate billing_webhook processing with a raw payload."""
    from arclane.api.routes.billing import billing_webhook

    import json
    from unittest.mock import MagicMock

    body = json.dumps(payload_dict).encode()

    request = MagicMock()
    request.headers = {
        "X-Service-Token": "test-token",
        "X-Webhook-Signature": "",
    }
    request.client.host = "127.0.0.1"
    request.body = AsyncMock(return_value=body)

    async with db() as session:
        # Patch settings to accept the token and skip HMAC
        with patch("arclane.api.routes.billing.settings") as mock_settings:
            mock_settings.zuul_service_token = "test-token"
            mock_settings.webhook_signing_secret = ""
            with patch("arclane.api.routes.billing.limiter"):
                result = await billing_webhook(request, session)
    return result


async def test_webhook_idempotency_skips_duplicate(db):
    """Second webhook with same event ID is skipped."""
    biz_id = await _create_business(db)

    payload = {
        "event": "credits.purchased",
        "customer_email": "test@test.com",
        "day_pack": "boost-5",
        "working_days_purchased": 5,
        "webhook_event_id": "evt_123abc",
    }

    result1 = await _process_webhook(db, payload)
    assert result1.get("status") == "ok"
    assert result1.get("working_days_added") == 5

    # Second call with same event ID
    result2 = await _process_webhook(db, payload)
    assert result2.get("duplicate") is True

    # Credits should only be added once
    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.working_days_bonus == 5  # not 10


async def test_webhook_without_event_id_always_processes(db):
    """Webhooks without event ID are always processed (backward compat)."""
    biz_id = await _create_business(db)

    payload = {
        "event": "credits.purchased",
        "customer_email": "test@test.com",
        "day_pack": "boost-5",
        "working_days_purchased": 5,
    }

    result1 = await _process_webhook(db, payload)
    assert result1.get("status") == "ok"

    result2 = await _process_webhook(db, payload)
    assert result2.get("status") == "ok"

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.working_days_bonus == 10  # processed twice


async def test_webhook_stores_event_id_in_activity(db):
    """Activity record includes webhook_event_id in metadata."""
    await _create_business(db)

    payload = {
        "event": "subscription.created",
        "customer_email": "test@test.com",
        "plan": "pro",
        "webhook_event_id": "evt_sub_456",
    }

    await _process_webhook(db, payload)

    async with db() as session:
        result = await session.execute(
            select(Activity).where(Activity.action == "Subscription active")
        )
        activity = result.scalar_one()
        assert activity.metadata_json is not None
        assert activity.metadata_json["webhook_event_id"] == "evt_sub_456"


async def test_different_event_ids_both_process(db):
    """Different event IDs for similar events are both processed."""
    await _create_business(db)

    payload1 = {
        "event": "credits.purchased",
        "customer_email": "test@test.com",
        "working_days_purchased": 5,
        "webhook_event_id": "evt_aaa",
    }
    payload2 = {
        "event": "credits.purchased",
        "customer_email": "test@test.com",
        "working_days_purchased": 5,
        "webhook_event_id": "evt_bbb",
    }

    result1 = await _process_webhook(db, payload1)
    result2 = await _process_webhook(db, payload2)
    assert result1.get("status") == "ok"
    assert result2.get("status") == "ok"

    async with db() as session:
        biz = (await session.execute(
            select(Business).where(Business.slug == "idempotent-biz")
        )).scalar_one()
        assert biz.working_days_bonus == 10
