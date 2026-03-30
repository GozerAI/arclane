"""Tests for plan-based priority execution ordering."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from arclane.engine.scheduler import PLAN_PRIORITY


def test_plan_priority_scale_highest():
    assert PLAN_PRIORITY["scale"] == 0
    assert PLAN_PRIORITY["enterprise"] == 0


def test_plan_priority_growth_before_pro():
    assert PLAN_PRIORITY["growth"] < PLAN_PRIORITY["pro"]


def test_plan_priority_pro_before_starter():
    assert PLAN_PRIORITY["pro"] < PLAN_PRIORITY["starter"]


def test_plan_priority_starter_before_preview():
    assert PLAN_PRIORITY["starter"] < PLAN_PRIORITY["preview"]


def test_plan_priority_ordering():
    plans = ["preview", "starter", "pro", "growth", "scale", "enterprise"]
    sorted_plans = sorted(plans, key=lambda p: PLAN_PRIORITY.get(p, 5))
    assert sorted_plans[:2] == ["scale", "enterprise"] or sorted_plans[0] in ("scale", "enterprise")
    assert sorted_plans[-1] == "preview"


def test_unknown_plan_gets_lowest_priority():
    unknown_priority = PLAN_PRIORITY.get("unknown_plan", 5)
    assert unknown_priority > PLAN_PRIORITY["preview"]


@pytest.mark.asyncio
async def test_nightly_cycle_orders_by_plan():
    """Verify that businesses are sorted by plan priority before execution."""
    from arclane.models.tables import Business

    # Create mock rows: (id, plan) in wrong order
    mock_rows = [
        (1, "preview"),
        (2, "scale"),
        (3, "starter"),
        (4, "pro"),
        (5, "growth"),
    ]

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    executed_ids = []

    async def mock_run_single(business_id, semaphore):
        executed_ids.append(business_id)

    with (
        patch("arclane.engine.scheduler.async_session", return_value=mock_session),
        patch("arclane.engine.scheduler._run_single_nightly", side_effect=mock_run_single),
    ):
        from arclane.engine.scheduler import _nightly_cycle
        await _nightly_cycle()

    # Scale (id=2) should be first, preview (id=1) should be last
    assert executed_ids[0] == 2  # scale
    assert executed_ids[-1] == 1  # preview


@pytest.mark.asyncio
async def test_nightly_cycle_all_tiers_execute():
    """All plan tiers should execute, not just high-priority ones."""
    mock_rows = [
        (1, "preview"),
        (2, "scale"),
        (3, "starter"),
    ]

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    executed_ids = []

    async def mock_run_single(business_id, semaphore):
        executed_ids.append(business_id)

    with (
        patch("arclane.engine.scheduler.async_session", return_value=mock_session),
        patch("arclane.engine.scheduler._run_single_nightly", side_effect=mock_run_single),
    ):
        from arclane.engine.scheduler import _nightly_cycle
        await _nightly_cycle()

    assert set(executed_ids) == {1, 2, 3}
