"""Tests for stuck cycle recovery and cycle optimizer."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Activity, Base, Business, Content, Cycle


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _create_business(factory, slug="test-biz", working_days=5):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.title(),
            description="Test business",
            owner_email="test@test.com",
            plan="starter",
            working_days_remaining=working_days,
            working_days_bonus=0,
        )
        session.add(biz)
        await session.commit()
        return biz.id


async def _create_cycle(factory, business_id, status="running", started_minutes_ago=60):
    async with factory() as session:
        started_at = datetime.now(timezone.utc) - timedelta(minutes=started_minutes_ago)
        cycle = Cycle(
            business_id=business_id,
            trigger="nightly",
            status=status,
            started_at=started_at,
        )
        session.add(cycle)
        await session.commit()
        return cycle.id


# --- Stuck cycle recovery ---


async def test_recover_stuck_cycles(db):
    """Cycles stuck in running state for over threshold are marked failed."""
    from arclane.engine.scheduler import _recover_stuck_cycles

    biz_id = await _create_business(db)
    cycle_id = await _create_cycle(db, biz_id, status="running", started_minutes_ago=60)

    with patch("arclane.engine.scheduler.async_session", db):
        await _recover_stuck_cycles()

    async with db() as session:
        cycle = await session.get(Cycle, cycle_id)
        assert cycle.status == "failed"
        assert cycle.completed_at is not None
        assert "auto-recovered" in (cycle.result or {}).get("recovery", "")


async def test_recovery_creates_activity(db):
    """Recovery creates an Activity record explaining the recovery."""
    from arclane.engine.scheduler import _recover_stuck_cycles

    biz_id = await _create_business(db)
    cycle_id = await _create_cycle(db, biz_id, status="running", started_minutes_ago=60)

    with patch("arclane.engine.scheduler.async_session", db):
        await _recover_stuck_cycles()

    async with db() as session:
        result = await session.execute(
            select(Activity).where(Activity.cycle_id == cycle_id)
        )
        activities = result.scalars().all()
        assert len(activities) == 1
        assert "recovered" in activities[0].action.lower()


async def test_recovery_skips_recent_cycles(db):
    """Cycles that started recently are not recovered."""
    from arclane.engine.scheduler import _recover_stuck_cycles

    biz_id = await _create_business(db)
    cycle_id = await _create_cycle(db, biz_id, status="running", started_minutes_ago=5)

    with patch("arclane.engine.scheduler.async_session", db):
        await _recover_stuck_cycles()

    async with db() as session:
        cycle = await session.get(Cycle, cycle_id)
        assert cycle.status == "running"  # untouched


async def test_recovery_skips_completed_cycles(db):
    """Completed cycles are never touched by recovery."""
    from arclane.engine.scheduler import _recover_stuck_cycles

    biz_id = await _create_business(db)
    cycle_id = await _create_cycle(db, biz_id, status="completed", started_minutes_ago=120)

    with patch("arclane.engine.scheduler.async_session", db):
        await _recover_stuck_cycles()

    async with db() as session:
        cycle = await session.get(Cycle, cycle_id)
        assert cycle.status == "completed"


async def test_recovery_handles_multiple_stuck(db):
    """Multiple stuck cycles across different businesses are all recovered."""
    from arclane.engine.scheduler import _recover_stuck_cycles

    biz1 = await _create_business(db, slug="biz-1")
    biz2 = await _create_business(db, slug="biz-2")
    c1 = await _create_cycle(db, biz1, status="running", started_minutes_ago=45)
    c2 = await _create_cycle(db, biz2, status="running", started_minutes_ago=90)

    with patch("arclane.engine.scheduler.async_session", db):
        await _recover_stuck_cycles()

    async with db() as session:
        cycle1 = await session.get(Cycle, c1)
        cycle2 = await session.get(Cycle, c2)
        assert cycle1.status == "failed"
        assert cycle2.status == "failed"


async def test_recovery_noop_when_none_stuck(db):
    """Recovery is a no-op when no cycles are stuck."""
    from arclane.engine.scheduler import _recover_stuck_cycles

    biz_id = await _create_business(db)
    await _create_cycle(db, biz_id, status="completed", started_minutes_ago=120)

    with patch("arclane.engine.scheduler.async_session", db):
        await _recover_stuck_cycles()  # Should not raise


# --- Cycle optimizer ---


async def test_optimizer_allows_healthy_business(db):
    """Optimizer allows cycles for businesses with normal history."""
    from arclane.autonomy.cycle_optimizer import evaluate_nightly

    biz_id = await _create_business(db)

    async with db() as session:
        biz = await session.get(Business, biz_id)
        decision = await evaluate_nightly(biz, session)
        assert decision.should_run is True


async def test_optimizer_blocks_consecutive_failures(db):
    """Optimizer blocks cycles after consecutive failures."""
    from arclane.autonomy.cycle_optimizer import evaluate_nightly, CONSECUTIVE_FAILURE_THRESHOLD

    biz_id = await _create_business(db)

    async with db() as session:
        for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
            session.add(Cycle(
                business_id=biz_id,
                trigger="nightly",
                status="failed",
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
                completed_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ))
        await session.commit()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        decision = await evaluate_nightly(biz, session)
        assert decision.should_run is False
        assert "failed" in decision.reason.lower()


async def test_optimizer_allows_after_mixed_results(db):
    """Optimizer allows cycles if failures are not all consecutive."""
    from arclane.autonomy.cycle_optimizer import evaluate_nightly

    biz_id = await _create_business(db)

    async with db() as session:
        # Most recent is completed, breaking the failure streak (old enough to pass recency check)
        session.add(Cycle(
            business_id=biz_id, trigger="nightly", status="completed",
            started_at=datetime.now(timezone.utc) - timedelta(hours=12),
            completed_at=datetime.now(timezone.utc) - timedelta(hours=10),
        ))
        session.add(Cycle(
            business_id=biz_id, trigger="nightly", status="failed",
            started_at=datetime.now(timezone.utc) - timedelta(hours=24),
            completed_at=datetime.now(timezone.utc) - timedelta(hours=23),
        ))
        await session.commit()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        decision = await evaluate_nightly(biz, session)
        assert decision.should_run is True


async def test_optimizer_blocks_too_recent_cycle(db):
    """Optimizer blocks if last cycle completed too recently."""
    from arclane.autonomy.cycle_optimizer import evaluate_nightly, MIN_HOURS_BETWEEN_CYCLES

    biz_id = await _create_business(db)

    async with db() as session:
        session.add(Cycle(
            business_id=biz_id, trigger="on_demand", status="completed",
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        ))
        await session.commit()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        decision = await evaluate_nightly(biz, session)
        assert decision.should_run is False
        assert "ago" in decision.reason


async def test_optimizer_suggests_content_for_new_business(db):
    """Optimizer suggests content focus for businesses with few cycles."""
    from arclane.autonomy.cycle_optimizer import evaluate_nightly

    biz_id = await _create_business(db)

    async with db() as session:
        session.add(Cycle(
            business_id=biz_id, trigger="nightly", status="completed",
            started_at=datetime.now(timezone.utc) - timedelta(hours=24),
            completed_at=datetime.now(timezone.utc) - timedelta(hours=23),
        ))
        await session.commit()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        decision = await evaluate_nightly(biz, session)
        assert decision.should_run is True
        assert decision.suggested_focus == "content"


async def test_optimizer_suggests_strategy_after_content_threshold(db):
    """Optimizer suggests strategy focus once enough content exists."""
    from arclane.autonomy.cycle_optimizer import evaluate_nightly, CONTENT_PIVOT_THRESHOLD

    biz_id = await _create_business(db)

    async with db() as session:
        for i in range(CONTENT_PIVOT_THRESHOLD):
            session.add(Content(
                business_id=biz_id,
                content_type="blog",
                title=f"Post {i}",
                body=f"Content {i}",
                status="draft",
            ))
        for i in range(5):
            session.add(Cycle(
                business_id=biz_id, trigger="nightly", status="completed",
                started_at=datetime.now(timezone.utc) - timedelta(days=i + 2),
                completed_at=datetime.now(timezone.utc) - timedelta(days=i + 2, hours=-1),
            ))
        await session.commit()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        decision = await evaluate_nightly(biz, session)
        assert decision.should_run is True
        assert decision.suggested_focus == "strategy"


# --- Nightly integration with optimizer ---


async def test_nightly_skips_when_optimizer_says_no(db):
    """Nightly cycle respects optimizer decision to skip."""
    from arclane.engine.scheduler import _nightly_cycle
    from arclane.autonomy.cycle_optimizer import CycleDecision

    await _create_business(db, slug="opt-skip", working_days=5)

    mock_decision = CycleDecision(should_run=False, reason="test skip")

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            mock_orch.next_queue_task = lambda biz: None
            with patch("arclane.autonomy.cycle_optimizer.evaluate_nightly", AsyncMock(return_value=mock_decision)):
                await _nightly_cycle()
                mock_orch.execute_cycle.assert_not_called()


async def test_nightly_runs_when_optimizer_says_yes(db):
    """Nightly cycle proceeds when optimizer approves."""
    from arclane.engine.scheduler import _nightly_cycle
    from arclane.autonomy.cycle_optimizer import CycleDecision

    await _create_business(db, slug="opt-run", working_days=5)

    mock_decision = CycleDecision(should_run=True, reason="ok")

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            mock_orch.next_queue_task = lambda biz: None
            with patch("arclane.autonomy.cycle_optimizer.evaluate_nightly", AsyncMock(return_value=mock_decision)):
                await _nightly_cycle()
                mock_orch.execute_cycle.assert_called_once()
