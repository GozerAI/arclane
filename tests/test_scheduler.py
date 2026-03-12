"""Tests for scheduler — nightly cycles + monthly credit reset."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business


@pytest.fixture
async def db():
    """Returns (engine, session_factory) so scheduler can use the factory."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


async def _create_business(factory, slug, plan="starter", credits=5, bonus=0):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.title(),
            description="Test",
            owner_email=f"{slug}@test.com",
            plan=plan,
            credits_remaining=credits,
            credits_bonus=bonus,
        )
        session.add(biz)
        await session.commit()
        return biz.id


async def test_monthly_credit_reset(db):
    """Monthly reset restores credits to plan allocation."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "reset-test", plan="pro", credits=2)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 20  # Pro plan = 20 credits


async def test_monthly_reset_skips_cancelled(db):
    """Cancelled businesses don't get credits."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "cancelled-test", plan="cancelled", credits=0)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 0


async def test_nightly_skips_no_credits(db):
    """Nightly cycle skips businesses with zero credits."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "no-credit", plan="starter", credits=0, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_not_called()


async def test_nightly_deducts_credit(db):
    """Nightly cycle deducts a credit when running."""
    from arclane.engine.scheduler import _nightly_cycle

    biz_id = await _create_business(db, "deduct-test", plan="starter", credits=3, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_called_once()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 2  # 3 - 1 = 2


async def test_monthly_reset_enterprise(db):
    """Enterprise plan gets 100 credits on reset."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "enterprise-test", plan="enterprise", credits=10)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 100
