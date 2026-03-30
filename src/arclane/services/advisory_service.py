"""Advisory service — AI-generated notes, warnings, celebrations, and weekly digest."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import (
    AdvisoryNote,
    Business,
    Content,
    Cycle,
    Milestone,
    RevenueEvent,
)

log = get_logger("advisory")


async def generate_advisory_notes(business: Business, session: AsyncSession) -> list[dict]:
    """Generate advisory notes based on current business state. Called post-cycle."""
    notes = []

    # Check for milestone celebrations
    recent_milestones = await _recently_completed_milestones(business, session, hours=24)
    for m in recent_milestones:
        notes.append({
            "category": "celebration",
            "title": f"Milestone achieved: {m.title}",
            "body": f"Congratulations! You completed '{m.title}' on day {business.roadmap_day} of your program.",
            "priority": 3,
        })

    # Check for overdue milestones
    overdue = await _overdue_milestones(business, session)
    for m in overdue:
        notes.append({
            "category": "warning",
            "title": f"Overdue: {m.title}",
            "body": f"This milestone was due by day {m.due_day}. Current day: {business.roadmap_day}. Consider prioritizing this.",
            "priority": 7,
        })

    # Check working day warnings
    total_working_days = business.working_days_remaining + business.working_days_bonus
    if total_working_days <= 3:
        notes.append({
            "category": "warning",
            "title": "Working days running low",
            "body": f"You have {total_working_days} working days remaining. Consider upgrading your plan to keep the program running.",
            "priority": 8,
        })

    # Phase graduation readiness
    if business.current_phase and 1 <= business.current_phase <= 4:
        from arclane.services.roadmap_service import check_phase_graduation
        check = await check_phase_graduation(business, session)
        if check["ready"]:
            notes.append({
                "category": "celebration",
                "title": f"Ready to advance to Phase {business.current_phase + 1 if business.current_phase < 4 else 'graduation'}!",
                "body": f"Phase {business.current_phase} graduation criteria met with score {check['score']}%.",
                "priority": 9,
            })
        elif check["score"] >= 70:
            notes.append({
                "category": "insight",
                "title": f"Phase {business.current_phase} nearly complete",
                "body": f"Score: {check['score']}%. Remaining: {', '.join(check['unmet'][:3])}.",
                "priority": 5,
            })

    # Content velocity check
    content_7d = await _content_count_since(business, session, days=7)
    if content_7d == 0 and (business.roadmap_day or 0) > 7:
        notes.append({
            "category": "recommendation",
            "title": "No content produced this week",
            "body": "Content production has stalled. Consistent content is key to building distribution and authority.",
            "priority": 6,
        })

    # Revenue milestone check
    total_revenue = await _total_revenue(business, session)
    if total_revenue > 0:
        thresholds = [100_00, 1000_00, 5000_00, 10000_00]  # cents
        for threshold in thresholds:
            if total_revenue >= threshold:
                # Only celebrate if not already celebrated
                existing = await session.execute(
                    select(func.count(AdvisoryNote.id)).where(
                        AdvisoryNote.business_id == business.id,
                        AdvisoryNote.title == f"Revenue milestone: ${threshold // 100:,}",
                    )
                )
                if (existing.scalar() or 0) == 0:
                    notes.append({
                        "category": "celebration",
                        "title": f"Revenue milestone: ${threshold // 100:,}",
                        "body": f"Total revenue has reached ${total_revenue / 100:,.2f}.",
                        "priority": 8,
                    })

    # Revenue-driven insights
    revenue_insights = await _revenue_insights(business, session)
    notes.extend(revenue_insights)

    # Health score gap recommendations
    health_recs = await _health_gap_recommendations(business, session)
    notes.extend(health_recs)

    # Persist notes
    created = []
    for note in notes:
        record = AdvisoryNote(
            business_id=business.id,
            category=note["category"],
            title=note["title"],
            body=note["body"],
            priority=note["priority"],
        )
        session.add(record)
        created.append(note)

    if created:
        await session.flush()
        log.info("Generated %d advisory notes for %s", len(created), business.slug)

    return created


async def generate_weekly_digest(business: Business, session: AsyncSession) -> dict:
    """Generate a weekly digest summary for the business owner."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    # Cycles this week
    cycles_result = await session.execute(
        select(Cycle).where(
            Cycle.business_id == business.id,
            Cycle.created_at >= week_ago,
        )
    )
    cycles = cycles_result.scalars().all()
    completed_cycles = [c for c in cycles if c.status == "completed"]

    # Content this week
    content_result = await session.execute(
        select(Content).where(
            Content.business_id == business.id,
            Content.created_at >= week_ago,
        )
    )
    content_items = content_result.scalars().all()

    # Milestones completed this week
    milestones_result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.completed_at != None,  # noqa: E711
            Milestone.completed_at >= week_ago,
        )
    )
    milestones = milestones_result.scalars().all()

    # Revenue this week
    revenue_result = await session.execute(
        select(func.sum(RevenueEvent.amount_cents)).where(
            RevenueEvent.business_id == business.id,
            RevenueEvent.event_date >= week_ago,
        )
    )
    weekly_revenue_cents = revenue_result.scalar() or 0

    # Unacknowledged advisory notes
    notes_result = await session.execute(
        select(AdvisoryNote).where(
            AdvisoryNote.business_id == business.id,
            AdvisoryNote.acknowledged == False,  # noqa: E712
        ).order_by(AdvisoryNote.priority.desc()).limit(5)
    )
    top_notes = notes_result.scalars().all()

    digest = {
        "period": {"start": week_ago.isoformat(), "end": now.isoformat()},
        "roadmap_day": business.roadmap_day,
        "current_phase": business.current_phase,
        "cycles": {"total": len(cycles), "completed": len(completed_cycles)},
        "content": {"produced": len(content_items), "types": list({c.content_type for c in content_items})},
        "milestones": {"completed": len(milestones), "names": [m.title for m in milestones]},
        "revenue": {"weekly_cents": weekly_revenue_cents, "weekly_usd": weekly_revenue_cents / 100},
        "top_notes": [
            {"category": n.category, "title": n.title, "priority": n.priority}
            for n in top_notes
        ],
    }

    return digest


async def check_warning_conditions(business: Business, session: AsyncSession) -> list[str]:
    """Return a list of active warning conditions."""
    warnings = []

    total_working_days = business.working_days_remaining + business.working_days_bonus
    if total_working_days <= 0:
        warnings.append("No working days remaining — nightly cycles are paused")
    elif total_working_days <= 3:
        warnings.append(f"Only {total_working_days} working days remaining")

    day = business.roadmap_day or 0
    phase = business.current_phase or 0
    if phase >= 1 and phase <= 4:
        phase_end = {1: 15, 2: 30, 3: 45, 4: 60}.get(phase, 60)
        if day > phase_end:
            warnings.append(f"Phase {phase} should have completed by day {phase_end} (current: day {day})")

    # Check for failed cycles recently
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    failed_count = (await session.execute(
        select(func.count(Cycle.id)).where(
            Cycle.business_id == business.id,
            Cycle.status == "failed",
            Cycle.created_at >= cutoff,
        )
    )).scalar() or 0
    if failed_count >= 2:
        warnings.append(f"{failed_count} cycles failed in the last 3 days")

    return warnings


async def _recently_completed_milestones(business: Business, session: AsyncSession, hours: int = 24):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.status == "completed",
            Milestone.completed_at != None,  # noqa: E711
            Milestone.completed_at >= cutoff,
        )
    )
    return result.scalars().all()


async def _overdue_milestones(business: Business, session: AsyncSession):
    day = business.roadmap_day or 0
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.status != "completed",
            Milestone.due_day != None,  # noqa: E711
            Milestone.due_day < day,
        )
    )
    return result.scalars().all()


async def _content_count_since(business: Business, session: AsyncSession, days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(func.count(Content.id)).where(
            Content.business_id == business.id,
            Content.created_at >= cutoff,
        )
    )
    return result.scalar() or 0


async def _total_revenue(business: Business, session: AsyncSession) -> int:
    result = await session.execute(
        select(func.sum(RevenueEvent.amount_cents)).where(
            RevenueEvent.business_id == business.id,
        )
    )
    return result.scalar() or 0


async def _revenue_insights(business: Business, session: AsyncSession) -> list[dict]:
    """Generate advisory notes based on revenue patterns."""
    insights = []

    # Check for revenue momentum
    now = datetime.now(timezone.utc)
    this_week = now - timedelta(days=7)
    last_week_start = now - timedelta(days=14)

    this_week_rev = (await session.execute(
        select(func.sum(RevenueEvent.amount_cents)).where(
            RevenueEvent.business_id == business.id,
            RevenueEvent.event_date >= this_week,
        )
    )).scalar() or 0

    last_week_rev = (await session.execute(
        select(func.sum(RevenueEvent.amount_cents)).where(
            RevenueEvent.business_id == business.id,
            RevenueEvent.event_date >= last_week_start,
            RevenueEvent.event_date < this_week,
        )
    )).scalar() or 0

    if this_week_rev > 0 and last_week_rev > 0:
        growth_pct = ((this_week_rev - last_week_rev) / last_week_rev) * 100
        if growth_pct >= 20:
            insights.append({
                "category": "celebration",
                "title": f"Revenue up {growth_pct:.0f}% week-over-week",
                "body": f"Revenue grew from ${last_week_rev / 100:,.2f} to ${this_week_rev / 100:,.2f}. Keep doing what's working.",
                "priority": 7,
            })
        elif growth_pct <= -20:
            insights.append({
                "category": "warning",
                "title": f"Revenue down {abs(growth_pct):.0f}% week-over-week",
                "body": f"Revenue dropped from ${last_week_rev / 100:,.2f} to ${this_week_rev / 100:,.2f}. Review acquisition channels.",
                "priority": 8,
            })

    # Check top revenue source
    top_source_result = await session.execute(
        select(RevenueEvent.source, func.sum(RevenueEvent.amount_cents))
        .where(RevenueEvent.business_id == business.id, RevenueEvent.event_date >= this_week)
        .group_by(RevenueEvent.source)
        .order_by(func.sum(RevenueEvent.amount_cents).desc())
        .limit(1)
    )
    top_source = top_source_result.first()
    if top_source and top_source[1] > 0:
        insights.append({
            "category": "insight",
            "title": f"Top revenue source: {top_source[0]}",
            "body": f"{top_source[0]} drove ${top_source[1] / 100:,.2f} this week. Consider doubling down on this channel.",
            "priority": 4,
        })

    return insights


async def _health_gap_recommendations(business: Business, session: AsyncSession) -> list[dict]:
    """Generate advisory notes for weak health areas."""
    recs = []
    try:
        from arclane.services.health_score_service import calculate_health_score
        result = await calculate_health_score(business, session)
        sub_scores = result.get("sub_scores", {})

        area_advice = {
            "market_fit": "Strengthen positioning by updating competitor profiles and refining the offer.",
            "content": "Increase content production — aim for 3+ pieces per week across multiple channels.",
            "revenue": "Focus on conversion: optimize the funnel, test pricing, or launch an outreach campaign.",
            "operations": "Improve cycle reliability — check for failed tasks and automation opportunities.",
            "momentum": "Increase activity: run cycles consistently and complete pending milestones.",
        }

        for area, score in sub_scores.items():
            if score < 35:
                recs.append({
                    "category": "recommendation",
                    "title": f"Weak area: {area.replace('_', ' ').title()} ({score:.0f}/100)",
                    "body": area_advice.get(area, f"Focus on improving {area.replace('_', ' ')}."),
                    "priority": 6,
                })
    except Exception:
        pass  # Health score calculation failed — skip recommendations

    return recs
