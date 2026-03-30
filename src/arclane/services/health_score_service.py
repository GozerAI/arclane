"""Business health score calculation and tracking."""

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
)

log = get_logger("health_score")

# Score weights
WEIGHTS = {
    "market_fit": 0.25,
    "content": 0.20,
    "revenue": 0.25,
    "operations": 0.15,
    "momentum": 0.15,
}


async def calculate_health_score(business: Business, session: AsyncSession) -> dict:
    """Calculate the overall health score (0-100) with sub-scores."""
    scores = {}
    factors = {}

    # Market fit score — based on milestone completion in strategy/market areas
    mf_score, mf_factors = await _market_fit_score(business, session)
    scores["market_fit"] = mf_score
    factors["market_fit"] = mf_factors

    # Content score — based on content production and diversity
    ct_score, ct_factors = await _content_score(business, session)
    scores["content"] = ct_score
    factors["content"] = ct_factors

    # Revenue score — based on revenue events
    rv_score, rv_factors = await _revenue_score(business, session)
    scores["revenue"] = rv_score
    factors["revenue"] = rv_factors

    # Operations score — based on cycle completion rate
    op_score, op_factors = await _operations_score(business, session)
    scores["operations"] = op_score
    factors["operations"] = op_factors

    # Momentum score — based on recent activity and milestone velocity
    mo_score, mo_factors = await _momentum_score(business, session)
    scores["momentum"] = mo_score
    factors["momentum"] = mo_factors

    overall = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    return {
        "overall": round(overall, 1),
        "sub_scores": {k: round(v, 1) for k, v in scores.items()},
        "factors": factors,
    }


async def record_health_snapshot(business: Business, session: AsyncSession) -> float:
    """Calculate and persist a health score snapshot. Returns the overall score."""
    result = await calculate_health_score(business, session)
    overall = result["overall"]

    # Record overall
    session.add(BusinessHealthScore(
        business_id=business.id,
        score_type="overall",
        score=overall,
        factors=result["factors"],
    ))

    # Record sub-scores
    for score_type, score_val in result["sub_scores"].items():
        session.add(BusinessHealthScore(
            business_id=business.id,
            score_type=score_type,
            score=score_val,
            factors=result["factors"].get(score_type),
        ))

    # Update business cached score
    business.health_score = overall
    await session.flush()

    log.info("Health snapshot recorded for %s: %.1f", business.slug, overall)
    return overall


async def get_health_trend(business: Business, session: AsyncSession, days: int = 30) -> list[dict]:
    """Return health score history for the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(BusinessHealthScore)
        .where(
            BusinessHealthScore.business_id == business.id,
            BusinessHealthScore.score_type == "overall",
            BusinessHealthScore.recorded_at >= cutoff,
        )
        .order_by(BusinessHealthScore.recorded_at)
    )
    snapshots = result.scalars().all()
    return [
        {
            "score": s.score,
            "factors": s.factors,
            "recorded_at": s.recorded_at.isoformat(),
        }
        for s in snapshots
    ]


async def _market_fit_score(business: Business, session: AsyncSession) -> tuple[float, dict]:
    """Score based on strategy and market milestones completed."""
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.key.like("p%-strategy%") | Milestone.key.like("p%-market%") | Milestone.key.like("p%-validation%") | Milestone.key.like("p%-competitor%"),
        )
    )
    milestones = result.scalars().all()
    if not milestones:
        return 20.0, {"reason": "No market milestones tracked yet", "completed": 0, "total": 0}

    completed = sum(1 for m in milestones if m.status == "completed")
    total = len(milestones)
    score = min(100, (completed / max(total, 1)) * 100 + 20)  # Base 20 for having milestones
    return score, {"completed": completed, "total": total}


async def _content_score(business: Business, session: AsyncSession) -> tuple[float, dict]:
    """Score based on content volume and diversity."""
    result = await session.execute(
        select(Content.content_type, func.count(Content.id))
        .where(Content.business_id == business.id)
        .group_by(Content.content_type)
    )
    type_counts = dict(result.all())
    total_content = sum(type_counts.values())
    type_diversity = len(type_counts)

    # Score: volume (up to 60) + diversity (up to 40)
    volume_score = min(60, total_content * 3)
    diversity_score = min(40, type_diversity * 10)
    score = volume_score + diversity_score
    return min(100, score), {"total": total_content, "types": type_counts, "diversity": type_diversity}


async def _revenue_score(business: Business, session: AsyncSession) -> tuple[float, dict]:
    """Score based on revenue events."""
    result = await session.execute(
        select(func.sum(RevenueEvent.amount_cents), func.count(RevenueEvent.id))
        .where(RevenueEvent.business_id == business.id)
    )
    row = result.one()
    total_cents = row[0] or 0
    event_count = row[1] or 0

    if event_count == 0:
        # Phase 1-2 businesses won't have revenue yet — give a base score
        phase = business.current_phase or 0
        if phase <= 2:
            return 40.0, {"reason": "Pre-revenue phase", "phase": phase}
        return 10.0, {"reason": "No revenue events recorded", "phase": phase}

    # Score based on amount and consistency
    amount_score = min(60, (total_cents / 100) * 0.5)  # $0.50 per point up to 60
    consistency_score = min(40, event_count * 5)
    score = amount_score + consistency_score
    return min(100, score), {"total_cents": total_cents, "events": event_count, "total_usd": total_cents / 100}


async def _operations_score(business: Business, session: AsyncSession) -> tuple[float, dict]:
    """Score based on cycle completion rate."""
    result = await session.execute(
        select(Cycle.status, func.count(Cycle.id))
        .where(Cycle.business_id == business.id)
        .group_by(Cycle.status)
    )
    status_counts = dict(result.all())
    total = sum(status_counts.values())
    completed = status_counts.get("completed", 0)

    if total == 0:
        return 50.0, {"reason": "No cycles run yet"}

    rate = completed / total
    score = rate * 100
    return round(score, 1), {"total_cycles": total, "completed": completed, "rate": round(rate, 3)}


async def _momentum_score(business: Business, session: AsyncSession) -> tuple[float, dict]:
    """Score based on recent activity (last 7 days)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    recent_cycles = (await session.execute(
        select(func.count(Cycle.id))
        .where(Cycle.business_id == business.id, Cycle.created_at >= cutoff)
    )).scalar() or 0

    recent_content = (await session.execute(
        select(func.count(Content.id))
        .where(Content.business_id == business.id, Content.created_at >= cutoff)
    )).scalar() or 0

    recent_milestones = (await session.execute(
        select(func.count(Milestone.id))
        .where(
            Milestone.business_id == business.id,
            Milestone.completed_at != None,  # noqa: E711
            Milestone.completed_at >= cutoff,
        )
    )).scalar() or 0

    # Score: cycles (up to 40) + content (up to 30) + milestones (up to 30)
    cycle_score = min(40, recent_cycles * 6)
    content_score = min(30, recent_content * 5)
    milestone_score = min(30, recent_milestones * 10)
    score = cycle_score + content_score + milestone_score
    return min(100, score), {
        "recent_cycles": recent_cycles,
        "recent_content": recent_content,
        "recent_milestones": recent_milestones,
    }
