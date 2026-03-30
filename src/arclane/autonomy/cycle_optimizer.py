"""Cycle optimizer — decides whether a nightly cycle is worth running and
which tasks to prioritize based on recent history and roadmap phase.

Used by the scheduler before committing a working day to a nightly cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import Activity, Business, Content, Cycle, Milestone

log = get_logger("autonomy.cycle_optimizer")

# Skip nightly if the last N cycles all failed — likely a systemic issue
CONSECUTIVE_FAILURE_THRESHOLD = 3

# Skip nightly if a cycle completed less than this many hours ago
MIN_HOURS_BETWEEN_CYCLES = 6

# Minimum content pieces before we consider shifting from content to strategy tasks
CONTENT_PIVOT_THRESHOLD = 10

# Phase end days — used for deadline urgency
PHASE_END_DAYS = {1: 21, 2: 45, 3: 75, 4: 90}

# Days before phase end to start prioritizing cycles
DEADLINE_URGENCY_WINDOW = 5


@dataclass
class CycleDecision:
    """Result of the optimizer's should-run analysis."""

    should_run: bool
    reason: str
    suggested_focus: str | None = None
    urgency: str = "normal"  # normal|elevated|critical


async def evaluate_nightly(business: Business, session: AsyncSession) -> CycleDecision:
    """Evaluate whether a nightly cycle should run for this business.

    Returns a CycleDecision indicating whether to proceed and why.
    """
    phase = getattr(business, "current_phase", None) or 0
    day = getattr(business, "roadmap_day", None) or 0

    # 1. Phase deadline urgency — override most skip conditions
    if 1 <= phase <= 4:
        phase_end = PHASE_END_DAYS.get(phase, 90)
        days_remaining = phase_end - day

        if days_remaining <= 0:
            # Past phase deadline — run to try catching up
            return CycleDecision(
                should_run=True,
                reason=f"Phase {phase} deadline passed (day {day}/{phase_end}) — catching up",
                urgency="critical",
            )

        if days_remaining <= DEADLINE_URGENCY_WINDOW:
            # Check if there are overdue milestones
            overdue_count = await _count_overdue_milestones(business, day, session)
            if overdue_count > 0:
                return CycleDecision(
                    should_run=True,
                    reason=f"Phase {phase} ends in {days_remaining} days with {overdue_count} overdue milestones",
                    urgency="critical",
                )
            return CycleDecision(
                should_run=True,
                reason=f"Phase {phase} ends in {days_remaining} days — deadline approaching",
                urgency="elevated",
            )

    # 2. Check for consecutive failures (relaxed near deadlines — already handled above)
    recent_cycles = (
        await session.execute(
            select(Cycle)
            .where(Cycle.business_id == business.id)
            .order_by(Cycle.created_at.desc())
            .limit(CONSECUTIVE_FAILURE_THRESHOLD)
        )
    ).scalars().all()

    if len(recent_cycles) >= CONSECUTIVE_FAILURE_THRESHOLD and all(
        c.status == "failed" for c in recent_cycles
    ):
        log.warning(
            "Skipping %s: last %d cycles all failed",
            business.slug, CONSECUTIVE_FAILURE_THRESHOLD,
        )
        return CycleDecision(
            should_run=False,
            reason=f"Last {CONSECUTIVE_FAILURE_THRESHOLD} cycles failed consecutively",
        )

    # 3. Check for too-recent cycle
    if recent_cycles:
        latest = recent_cycles[0]
        if latest.completed_at:
            completed = latest.completed_at
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            hours_since = (
                datetime.now(timezone.utc) - completed
            ).total_seconds() / 3600
            if hours_since < MIN_HOURS_BETWEEN_CYCLES:
                return CycleDecision(
                    should_run=False,
                    reason=f"Last cycle completed {hours_since:.1f}h ago (min {MIN_HOURS_BETWEEN_CYCLES}h)",
                )

    # 4. Check graduation readiness — if close, prioritize remaining milestones
    if 1 <= phase <= 4:
        graduation_urgency = await _check_graduation_urgency(business, session)
        if graduation_urgency:
            return graduation_urgency

    # 5. Suggest focus area based on phase and content balance
    focus = await _suggest_focus(business, phase, session)

    return CycleDecision(should_run=True, reason="ok", suggested_focus=focus)


async def _count_overdue_milestones(
    business: Business, current_day: int, session: AsyncSession
) -> int:
    """Count milestones that are past their due_day and not completed."""
    result = await session.execute(
        select(func.count(Milestone.id)).where(
            Milestone.business_id == business.id,
            Milestone.status != "completed",
            Milestone.due_day != None,  # noqa: E711
            Milestone.due_day < current_day,
        )
    )
    return result.scalar() or 0


async def _check_graduation_urgency(
    business: Business, session: AsyncSession
) -> CycleDecision | None:
    """Check if the business is close to being graduation-ready and should prioritize."""
    phase = getattr(business, "current_phase", 0) or 0
    if phase < 1 or phase > 4:
        return None

    # Count completed vs total milestones for current phase
    total_result = await session.execute(
        select(func.count(Milestone.id)).where(
            Milestone.business_id == business.id,
            Milestone.phase_number == phase,
        )
    )
    total = total_result.scalar() or 0

    completed_result = await session.execute(
        select(func.count(Milestone.id)).where(
            Milestone.business_id == business.id,
            Milestone.phase_number == phase,
            Milestone.status == "completed",
        )
    )
    completed = completed_result.scalar() or 0

    if total > 0 and completed > 0:
        completion_pct = completed / total
        remaining = total - completed
        if completion_pct >= 0.7 and remaining <= 3:
            return CycleDecision(
                should_run=True,
                reason=f"Phase {phase} is {completion_pct:.0%} complete — {remaining} milestones to graduation",
                urgency="elevated",
                suggested_focus="milestone_completion",
            )

    return None


async def _suggest_focus(
    business: Business, phase: int, session: AsyncSession
) -> str | None:
    """Suggest what the next cycle should focus on based on phase and history."""
    # Post-graduation: defer to ongoing optimizer
    if phase >= 5:
        return "adaptive"

    content_count = (
        await session.execute(
            select(func.count(Content.id)).where(Content.business_id == business.id)
        )
    ).scalar() or 0

    completed_cycles = (
        await session.execute(
            select(func.count(Cycle.id)).where(
                Cycle.business_id == business.id,
                Cycle.status == "completed",
            )
        )
    ).scalar() or 0

    # Phase-aware focus suggestions
    if phase == 1:
        # Phase 1: prioritize content and strategy to build the foundation
        if completed_cycles <= 2:
            return "content"
        return "strategy"

    if phase == 2:
        # Phase 2: prioritize validation and distribution
        return "validation"

    if phase == 3:
        # Phase 3: prioritize revenue and acquisition
        if content_count < 25:
            return "content"
        return "revenue"

    if phase == 4:
        # Phase 4: prioritize investor materials and reports
        return "graduation"

    # Default: early businesses need content first
    if completed_cycles <= 2:
        return "content"

    # Once enough content exists, shift toward strategy and operations
    if content_count >= CONTENT_PIVOT_THRESHOLD:
        return "strategy"

    return None
