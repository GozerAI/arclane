"""Scheduler jobs — nightly cycles, monthly reset, health checks, edge cases."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business, Cycle


@pytest.fixture
async def db():
    """Returns session factory for direct DB operations."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _create_business(factory, slug, plan="starter", credits=5, bonus=0, email="test@test.com"):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.title(),
            description="Test business",
            owner_email=email,
            plan=plan,
            credits_remaining=credits,
            credits_bonus=bonus,
        )
        session.add(biz)
        await session.commit()
        return biz.id


# --- Nightly cycle execution ---


async def test_nightly_runs_for_active_businesses(db):
    """Nightly cycle runs for businesses with credits."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "active-biz", credits=5, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_called_once()


async def test_nightly_skips_zero_credits(db):
    """Nightly cycle skips businesses with zero credits."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "broke-biz", credits=0, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_not_called()


async def test_nightly_skips_cancelled_plan(db):
    """Nightly cycle skips cancelled businesses."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "cancelled-biz", plan="cancelled", credits=5, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_not_called()


async def test_nightly_deducts_one_credit(db):
    """Nightly cycle deducts exactly one credit per run."""
    from arclane.engine.scheduler import _nightly_cycle

    biz_id = await _create_business(db, "deduct-one", credits=5, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 4


async def test_nightly_creates_cycle_record(db):
    """Nightly cycle creates a Cycle record with trigger=nightly."""
    from arclane.engine.scheduler import _nightly_cycle

    biz_id = await _create_business(db, "cycle-rec", credits=5, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()

    async with db() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Cycle).where(Cycle.business_id == biz_id)
        )
        cycles = result.scalars().all()
        assert len(cycles) == 1
        assert cycles[0].trigger == "nightly"
        assert cycles[0].status == "pending"


async def test_nightly_runs_multiple_businesses(db):
    """Nightly cycle runs for all eligible businesses."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "multi1", credits=3, bonus=0)
    await _create_business(db, "multi2", credits=5, bonus=0)
    await _create_business(db, "multi3", credits=1, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()
            assert mock_orch.execute_cycle.call_count == 3


async def test_nightly_deducts_bonus_before_regular(db):
    """Nightly deducts bonus credits before regular credits."""
    from arclane.engine.scheduler import _nightly_cycle

    biz_id = await _create_business(db, "bonus-first", credits=5, bonus=2)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_bonus == 1  # 2 - 1
        assert biz.credits_remaining == 5  # untouched


async def test_nightly_handles_orchestrator_exception(db):
    """Nightly continues even when orchestrator raises for a business."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "fail-biz", credits=5, bonus=0)
    await _create_business(db, "ok-biz", credits=5, bonus=0)

    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Orchestrator failed")

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock(side_effect=side_effect)
            await _nightly_cycle()
            # Both businesses should be attempted
            assert mock_orch.execute_cycle.call_count == 2


# --- Monthly credit reset ---


async def test_monthly_reset_all_plans(db):
    """Monthly reset restores correct credits for each plan."""
    from arclane.engine.scheduler import _monthly_credit_reset, PLAN_CREDITS

    ids = {}
    for plan, expected in PLAN_CREDITS.items():
        ids[plan] = await _create_business(db, f"plan-{plan}", plan=plan, credits=1)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        for plan, expected in PLAN_CREDITS.items():
            biz = await session.get(Business, ids[plan])
            assert biz.credits_remaining == expected, f"{plan} should get {expected} credits"


async def test_monthly_reset_skips_unknown_plan(db):
    """Unknown plan type is not modified by monthly reset."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "custom-plan", plan="custom", credits=99)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 99  # unchanged


async def test_monthly_reset_skips_cancelled(db):
    """Cancelled businesses get no credits on reset."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "canc", plan="cancelled", credits=0)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 0


# --- Container health check ---


async def test_container_health_check_runs(db):
    """Container health check queries deployed businesses."""
    from arclane.engine.scheduler import _container_health_check

    async with db() as session:
        biz = Business(
            slug="healthy-biz",
            name="Healthy Biz",
            description="Test",
            owner_email="test@test.com",
            app_deployed=True,
            container_id="abc123",
        )
        session.add(biz)
        await session.commit()

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.provisioning.deploy.check_container_health", new_callable=AsyncMock) as mock_health:
            mock_health.return_value = {"status": "running", "running": True}
            await _container_health_check()
            mock_health.assert_called_once_with("healthy-biz")


async def test_container_health_check_skips_undeployed(db):
    """Health check only queries deployed businesses with container IDs."""
    from arclane.engine.scheduler import _container_health_check

    async with db() as session:
        biz = Business(
            slug="no-deploy",
            name="No Deploy",
            description="Test",
            owner_email="test@test.com",
            app_deployed=False,
            container_id=None,
        )
        session.add(biz)
        await session.commit()

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.provisioning.deploy.check_container_health", new_callable=AsyncMock) as mock_health:
            await _container_health_check()
            mock_health.assert_not_called()


# --- Scheduler start/stop ---


async def test_scheduler_start_and_stop():
    """Scheduler starts and stops without error."""
    from arclane.engine.scheduler import start_scheduler, stop_scheduler
    from arclane.engine import scheduler as sched_mod

    start_scheduler()
    assert sched_mod._scheduler is not None

    stop_scheduler()
    assert sched_mod._scheduler is None


def test_stop_scheduler_when_not_started():
    """Stopping scheduler when not started is a no-op."""
    from arclane.engine.scheduler import stop_scheduler
    from arclane.engine import scheduler as sched_mod

    sched_mod._scheduler = None
    stop_scheduler()  # Should not raise
    assert sched_mod._scheduler is None


# --- PLAN_CREDITS mapping ---


def test_plan_credits_mapping():
    """PLAN_CREDITS has correct values for all plans."""
    from arclane.engine.scheduler import PLAN_CREDITS

    assert PLAN_CREDITS["starter"] == 10
    assert PLAN_CREDITS["pro"] == 20
    assert PLAN_CREDITS["growth"] == 75
    assert PLAN_CREDITS["scale"] == 150
    assert "cancelled" not in PLAN_CREDITS
