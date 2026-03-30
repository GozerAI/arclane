"""Roadmap forecaster — velocity tracking, graduation ETA, bottleneck detection.

Transforms Arclane from a task runner into a smart guide by answering:
- "Am I on track?"
- "When will I graduate?"
- "What's my biggest bottleneck?"
- "What should I focus on this week?"
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import (
    Business,
    BusinessHealthScore,
    Content,
    Cycle,
    Milestone,
    RevenueEvent,
    RoadmapPhase,
)
from arclane.services.roadmap_service import PHASES, PHASE_MILESTONES

log = get_logger("roadmap_forecaster")


async def compute_forecast(business: Business, session: AsyncSession) -> dict:
    """Compute a full roadmap forecast for a business.

    Returns velocity, ETA, pace status, bottlenecks, and weekly focus recommendation.
    """
    day = business.roadmap_day or 0
    phase = business.current_phase or 0

    velocity = await _compute_velocity(business, session)
    eta = _estimate_graduation(day, phase, velocity)
    pace = _assess_pace(day, phase, velocity)
    bottlenecks = await _detect_bottlenecks(business, session)
    weekly_focus = await _recommend_weekly_focus(business, session, bottlenecks)
    streak = await _compute_streak(business, session)

    return {
        "roadmap_day": day,
        "current_phase": phase,
        "velocity": velocity,
        "graduation_eta": eta,
        "pace": pace,
        "bottlenecks": bottlenecks,
        "weekly_focus": weekly_focus,
        "streak": streak,
    }


async def _compute_velocity(business: Business, session: AsyncSession) -> dict:
    """Compute milestone completion velocity (milestones per week)."""
    # Get all completed milestones with timestamps
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.status == "completed",
            Milestone.completed_at != None,  # noqa: E711
        ).order_by(Milestone.completed_at)
    )
    completed = result.scalars().all()

    total_milestones = sum(len(ms) for ms in PHASE_MILESTONES.values())
    completed_count = len(completed)

    if completed_count == 0:
        return {
            "milestones_per_week": 0,
            "completed": 0,
            "total": total_milestones,
            "completion_pct": 0,
        }

    # Calculate velocity over last 14 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    recent = [
        m for m in completed
        if m.completed_at and (
            m.completed_at.replace(tzinfo=timezone.utc) if m.completed_at.tzinfo is None else m.completed_at
        ) >= cutoff
    ]
    recent_velocity = len(recent) / 2  # per week (14 days = 2 weeks)

    # Overall velocity
    day = business.roadmap_day or 1
    weeks_elapsed = max(day / 7, 0.5)
    overall_velocity = completed_count / weeks_elapsed

    return {
        "milestones_per_week": round(recent_velocity, 1),
        "overall_velocity": round(overall_velocity, 1),
        "completed": completed_count,
        "total": total_milestones,
        "completion_pct": round(completed_count / total_milestones * 100, 1),
    }


def _estimate_graduation(day: int, phase: int, velocity: dict) -> dict:
    """Estimate when the business will graduate based on current velocity."""
    if phase >= 5:
        return {"status": "graduated", "days_remaining": 0, "estimated_date": None}

    total = velocity.get("total", 38)
    completed = velocity.get("completed", 0)
    remaining = total - completed
    weekly_rate = velocity.get("milestones_per_week", 0)

    if weekly_rate <= 0:
        # No recent velocity — estimate from overall
        weekly_rate = velocity.get("overall_velocity", 0)

    if weekly_rate <= 0:
        return {
            "status": "insufficient_data",
            "days_remaining": 90 - day,
            "estimated_date": None,
            "message": "Not enough data to predict — complete more milestones.",
        }

    weeks_remaining = remaining / weekly_rate
    days_remaining = int(weeks_remaining * 7)
    estimated_date = (datetime.now(timezone.utc) + timedelta(days=days_remaining)).strftime("%Y-%m-%d")

    # Compare to ideal pace (90 days)
    ideal_remaining = 90 - day
    delta = days_remaining - ideal_remaining

    if delta <= -7:
        status = "ahead_of_schedule"
        message = f"At this pace, you'll graduate {abs(delta)} days early."
    elif delta <= 7:
        status = "on_track"
        message = "You're on track to graduate within the 90-day program."
    else:
        status = "behind_schedule"
        message = f"At this pace, graduation will take {delta} extra days. Focus on completing milestones."

    return {
        "status": status,
        "days_remaining": days_remaining,
        "estimated_date": estimated_date,
        "ideal_remaining": ideal_remaining,
        "delta_days": delta,
        "message": message,
    }


def _assess_pace(day: int, phase: int, velocity: dict) -> dict:
    """Assess whether the business is ahead, on track, or behind."""
    if phase >= 5:
        return {"status": "graduated", "label": "Program Complete"}

    # Expected completion % by current day
    expected_pct = min(100, (day / 90) * 100)
    actual_pct = velocity.get("completion_pct", 0)
    gap = actual_pct - expected_pct

    if gap >= 10:
        status = "ahead"
        label = f"Ahead by {gap:.0f}% — excellent momentum"
        emoji_hint = "rocket"
    elif gap >= -10:
        status = "on_track"
        label = "On track — keep it up"
        emoji_hint = "check"
    elif gap >= -25:
        status = "slightly_behind"
        label = f"Slightly behind ({abs(gap):.0f}%) — focus on overdue milestones"
        emoji_hint = "warning"
    else:
        status = "significantly_behind"
        label = f"Behind by {abs(gap):.0f}% — needs immediate attention"
        emoji_hint = "alert"

    return {
        "status": status,
        "label": label,
        "expected_pct": round(expected_pct, 1),
        "actual_pct": round(actual_pct, 1),
        "gap_pct": round(gap, 1),
        "emoji_hint": emoji_hint,
    }


async def _detect_bottlenecks(business: Business, session: AsyncSession) -> list[dict]:
    """Detect bottlenecks — areas where milestones are stalling."""
    day = business.roadmap_day or 0
    phase = business.current_phase or 0

    if phase < 1:
        return []

    # Get incomplete milestones grouped by area pattern
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.status != "completed",
        ).order_by(Milestone.due_day)
    )
    incomplete = result.scalars().all()

    bottlenecks = []

    # Check for overdue milestones
    overdue = [m for m in incomplete if m.due_day and m.due_day < day]
    if overdue:
        bottlenecks.append({
            "type": "overdue_milestones",
            "severity": "high" if len(overdue) >= 3 else "medium",
            "count": len(overdue),
            "milestones": [{"key": m.key, "title": m.title, "due_day": m.due_day} for m in overdue[:5]],
            "recommendation": f"Complete {overdue[0].title} first — it's {day - overdue[0].due_day} days overdue.",
        })

    # Check content production rate
    content_count = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id == business.id)
    )).scalar() or 0

    # Expected content by phase
    expected_content = {1: 3, 2: 10, 3: 25, 4: 30}.get(phase, 3)
    if content_count < expected_content * 0.5:
        bottlenecks.append({
            "type": "content_deficit",
            "severity": "high" if content_count < expected_content * 0.3 else "medium",
            "current": content_count,
            "expected": expected_content,
            "recommendation": f"Content production is behind — {content_count}/{expected_content} pieces. Prioritize content tasks.",
        })

    # Check cycle success rate (last 7 days)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    cycle_result = await session.execute(
        select(Cycle.status, func.count(Cycle.id))
        .where(Cycle.business_id == business.id, Cycle.created_at >= week_ago)
        .group_by(Cycle.status)
    )
    cycle_counts = dict(cycle_result.all())
    total_cycles = sum(cycle_counts.values())
    failed_cycles = cycle_counts.get("failed", 0)
    if total_cycles > 0 and failed_cycles / total_cycles > 0.3:
        bottlenecks.append({
            "type": "high_failure_rate",
            "severity": "high",
            "failed": failed_cycles,
            "total": total_cycles,
            "recommendation": "Over 30% of recent cycles are failing. Check task configurations.",
        })

    # Check revenue (Phase 3+)
    if phase >= 3:
        rev_count = (await session.execute(
            select(func.count(RevenueEvent.id)).where(RevenueEvent.business_id == business.id)
        )).scalar() or 0
        if rev_count == 0:
            bottlenecks.append({
                "type": "no_revenue",
                "severity": "high" if phase >= 4 else "medium",
                "recommendation": "No revenue events recorded. Set up revenue tracking and connect payment webhooks.",
            })

    return bottlenecks


async def _recommend_weekly_focus(
    business: Business, session: AsyncSession, bottlenecks: list[dict],
) -> dict:
    """Generate a weekly focus recommendation based on phase, bottlenecks, and health."""
    phase = business.current_phase or 0
    day = business.roadmap_day or 0

    # Base recommendation per phase
    phase_focus = {
        1: {"area": "foundation", "action": "Complete your core strategy and launch your landing page"},
        2: {"area": "validation", "action": "Validate demand through outreach and distribution"},
        3: {"area": "growth", "action": "Build repeatable acquisition and scale content"},
        4: {"area": "graduation", "action": "Compile your results and prepare for post-program"},
        5: {"area": "optimization", "action": "Focus on the weakest health score area"},
    }

    base = phase_focus.get(phase, {"area": "general", "action": "Keep executing your plan"})

    # Override with bottleneck if critical
    if bottlenecks:
        top = bottlenecks[0]
        if top.get("severity") == "high":
            return {
                "area": top.get("type", base["area"]),
                "action": top.get("recommendation", base["action"]),
                "urgency": "high",
                "reason": f"Bottleneck detected: {top['type']}",
            }

    # Get upcoming milestones due this week
    upcoming_result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.status != "completed",
            Milestone.due_day != None,  # noqa: E711
            Milestone.due_day <= day + 7,
            Milestone.due_day >= day,
        ).order_by(Milestone.due_day).limit(3)
    )
    upcoming = upcoming_result.scalars().all()

    if upcoming:
        return {
            "area": base["area"],
            "action": f"This week: complete {upcoming[0].title}" + (f" and {len(upcoming)-1} more" if len(upcoming) > 1 else ""),
            "urgency": "normal",
            "upcoming_milestones": [{"key": m.key, "title": m.title, "due_day": m.due_day} for m in upcoming],
        }

    return {**base, "urgency": "normal"}


async def _compute_streak(business: Business, session: AsyncSession) -> dict:
    """Compute the current cycle completion streak."""
    result = await session.execute(
        select(Cycle).where(
            Cycle.business_id == business.id,
            Cycle.status.in_(["completed", "failed"]),
        ).order_by(Cycle.created_at.desc()).limit(30)
    )
    cycles = result.scalars().all()

    streak = 0
    for cycle in cycles:
        if cycle.status == "completed":
            streak += 1
        else:
            break

    longest = 0
    current = 0
    for cycle in reversed(cycles):
        if cycle.status == "completed":
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    return {
        "current": streak,
        "longest": longest,
        "total_completed": sum(1 for c in cycles if c.status == "completed"),
    }
