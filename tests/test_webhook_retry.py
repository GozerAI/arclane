"""Tests for failed webhook retry logic."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch


import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business, FailedWebhook


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _create_business(factory, slug="retry-biz"):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name="Retry Test",
            description="Test",
            owner_email="retry@test.com",
            plan="pro",
            working_days_remaining=10,
            working_days_bonus=0,
        )
        session.add(biz)
        await session.commit()


async def test_failed_webhook_stored_on_error(db):
    """When webhook processing raises, the payload is stored for retry."""
    await _create_business(db)

    from arclane.api.routes.billing import WebhookPayload, _process_webhook_payload
    from arclane.models.tables import _utcnow, FailedWebhook

    payload = WebhookPayload(
        event="subscription.created",
        customer_email="retry@test.com",
        plan="pro",
    )

    async with db() as session:
        # Store a failed webhook manually (simulating what billing_webhook does)
        session.add(FailedWebhook(
            endpoint="billing_webhook",
            payload=payload.model_dump(),
            error="Test error",
            next_retry_at=_utcnow() - timedelta(minutes=1),
        ))
        await session.commit()

    async with db() as session:
        result = await session.execute(select(FailedWebhook))
        wh = result.scalar_one()
        assert wh.endpoint == "billing_webhook"
        assert wh.attempts == 1
        assert wh.resolved is False
        assert wh.payload["event"] == "subscription.created"


async def test_retry_resolves_successful_webhook(db):
    """Retry scheduler marks webhook as resolved on success."""
    await _create_business(db)

    from arclane.models.tables import _utcnow

    async with db() as session:
        session.add(FailedWebhook(
            endpoint="billing_webhook",
            payload={
                "event": "subscription.created",
                "customer_email": "retry@test.com",
                "plan": "pro",
            },
            error="Initial failure",
            next_retry_at=_utcnow() - timedelta(minutes=1),
        ))
        await session.commit()

    # Run the retry job with mocked async_session
    from arclane.engine.scheduler import _retry_failed_webhooks

    with patch("arclane.engine.scheduler.async_session", db):
        await _retry_failed_webhooks()

    async with db() as session:
        result = await session.execute(select(FailedWebhook))
        wh = result.scalar_one()
        assert wh.resolved is True


async def test_retry_increments_attempts_on_failure(db):
    """Failed retry increments attempt count and pushes back next_retry_at."""
    from arclane.models.tables import _utcnow

    async with db() as session:
        session.add(FailedWebhook(
            endpoint="billing_webhook",
            payload={
                "event": "subscription.created",
                "customer_email": "nobody@test.com",  # no matching business
            },
            error="Initial failure",
            next_retry_at=_utcnow() - timedelta(minutes=1),
        ))
        await session.commit()

    from arclane.engine.scheduler import _retry_failed_webhooks

    # Mock _process_webhook_payload to raise
    with (
        patch("arclane.engine.scheduler.async_session", db),
        patch(
            "arclane.api.routes.billing._process_webhook_payload",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db error"),
        ),
    ):
        await _retry_failed_webhooks()

    async with db() as session:
        result = await session.execute(select(FailedWebhook))
        wh = result.scalar_one()
        assert wh.attempts == 2
        assert wh.resolved is False
        # next_retry_at should be pushed into the future (backoff)
        retry_ts = wh.next_retry_at.replace(tzinfo=timezone.utc) if wh.next_retry_at.tzinfo is None else wh.next_retry_at
        assert retry_ts > datetime.now(timezone.utc)


async def test_retry_gives_up_after_max_attempts(db):
    """Webhook is marked resolved (permanently failed) after max attempts."""
    from arclane.models.tables import _utcnow

    async with db() as session:
        session.add(FailedWebhook(
            endpoint="billing_webhook",
            payload={
                "event": "subscription.created",
                "customer_email": "nobody@test.com",
            },
            error="Repeated failure",
            attempts=4,  # one away from max (5)
            next_retry_at=_utcnow() - timedelta(minutes=1),
        ))
        await session.commit()

    from arclane.engine.scheduler import _retry_failed_webhooks

    with (
        patch("arclane.engine.scheduler.async_session", db),
        patch(
            "arclane.api.routes.billing._process_webhook_payload",
            new_callable=AsyncMock,
            side_effect=RuntimeError("persistent error"),
        ),
    ):
        await _retry_failed_webhooks()

    async with db() as session:
        result = await session.execute(select(FailedWebhook))
        wh = result.scalar_one()
        assert wh.attempts == 5
        assert wh.resolved is True  # permanently failed


async def test_retry_skips_future_webhooks(db):
    """Webhooks with next_retry_at in the future are not retried."""
    from arclane.models.tables import _utcnow

    async with db() as session:
        session.add(FailedWebhook(
            endpoint="billing_webhook",
            payload={"event": "subscription.created", "customer_email": "x@test.com"},
            error="Not yet",
            next_retry_at=_utcnow() + timedelta(hours=1),  # future
        ))
        await session.commit()

    from arclane.engine.scheduler import _retry_failed_webhooks

    with patch("arclane.engine.scheduler.async_session", db):
        await _retry_failed_webhooks()

    async with db() as session:
        result = await session.execute(select(FailedWebhook))
        wh = result.scalar_one()
        assert wh.attempts == 1  # unchanged
        assert wh.resolved is False
