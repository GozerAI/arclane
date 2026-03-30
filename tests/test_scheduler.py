"""Tests for scheduler — nightly cycles + monthly working day reset."""

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


async def _create_business(factory, slug, plan="starter", working_days=5, bonus=0):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.title(),
            description="Test",
            owner_email=f"{slug}@test.com",
            plan=plan,
            working_days_remaining=working_days,
            working_days_bonus=bonus,
        )
        session.add(biz)
        await session.commit()
        return biz.id


async def test_monthly_working_day_reset(db):
    """Monthly reset restores working days to plan allocation."""
    from arclane.engine.scheduler import _monthly_working_day_reset

    biz_id = await _create_business(db, "reset-test", plan="pro", working_days=2)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_working_day_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.working_days_remaining == 20  # Pro plan = 20 working days


async def test_monthly_reset_skips_cancelled(db):
    """Cancelled businesses don't get working days."""
    from arclane.engine.scheduler import _monthly_working_day_reset

    biz_id = await _create_business(db, "cancelled-test", plan="cancelled", working_days=0)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_working_day_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.working_days_remaining == 0


async def test_nightly_skips_no_working_days(db):
    """Nightly cycle skips businesses with zero working days."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "no-working-day", plan="starter", working_days=0, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_not_called()


async def test_nightly_deducts_working_day(db):
    """Nightly cycle deducts a working day when running."""
    from arclane.engine.scheduler import _nightly_cycle

    biz_id = await _create_business(db, "deduct-test", plan="starter", working_days=3, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_called_once()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.working_days_remaining == 2  # 3 - 1 = 2


async def test_monthly_reset_scale(db):
    """Scale plan gets 150 working days on reset."""
    from arclane.engine.scheduler import _monthly_working_day_reset

    biz_id = await _create_business(db, "scale-test", plan="scale", working_days=10)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_working_day_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.working_days_remaining == 150
