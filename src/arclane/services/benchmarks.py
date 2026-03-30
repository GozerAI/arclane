"""Cohort benchmarking — percentile comparison across businesses.

Compares a business's performance against the Arclane cohort to provide
context: "Is my 30% conversion rate good?" → "You're in the 75th percentile."
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
)

log = get_logger("benchmarks")


async def compute_benchmarks(business: Business, session: AsyncSession) -> dict:
    """Compute benchmark comparison for a business against the cohort."""
    biz_metrics = await _business_metrics(business, session)
    cohort_metrics = await _cohort_metrics(business, session)

    comparisons = {}
    for metric_name, biz_value in biz_metrics.items():
        cohort_values = cohort_metrics.get(metric_name, [])
        percentile = _calculate_percentile(biz_value, cohort_values)
        comparisons[metric_name] = {
            "value": biz_value,
            "percentile": percentile,
            "cohort_median": _median(cohort_values) if cohort_values else 0,
            "cohort_size": len(cohort_values),
            "assessment": _assess_percentile(percentile),
        }

    return {
        "business": business.name,
        "roadmap_day": business.roadmap_day,
        "current_phase": business.current_phase,
        "metrics": comparisons,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _business_metrics(business: Business, session: AsyncSession) -> dict:
    """Gather key metrics for a single business."""
    metrics = {}

    # Content production
    content_count = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id == business.id)
    )).scalar() or 0
    metrics["content_total"] = content_count

    # Content per day (velocity)
    day = max(business.roadmap_day or 1, 1)
    metrics["content_per_day"] = round(content_count / day, 2)

    # Cycle completion rate
    total_cycles = (await session.execute(
        select(func.count(Cycle.id)).where(Cycle.business_id == business.id)
    )).scalar() or 0
    completed_cycles = (await session.execute(
        select(func.count(Cycle.id)).where(
            Cycle.business_id == business.id, Cycle.status == "completed",
        )
    )).scalar() or 0
    metrics["cycle_completion_rate"] = round(completed_cycles / max(total_cycles, 1), 3)

    # Milestones completed
    milestones_done = (await session.execute(
        select(func.count(Milestone.id)).where(
            Milestone.business_id == business.id, Milestone.status == "completed",
        )
    )).scalar() or 0
    metrics["milestones_completed"] = milestones_done

    # Revenue (total cents)
    revenue = (await session.execute(
        select(func.sum(RevenueEvent.amount_cents)).where(
            RevenueEvent.business_id == business.id,
        )
    )).scalar() or 0
    metrics["revenue_cents"] = revenue

    # Health score
    metrics["health_score"] = business.health_score or 0

    return metrics


async def _cohort_metrics(business: Business, session: AsyncSession) -> dict:
    """Gather metrics for all businesses in the same phase (cohort).

    Uses businesses in the same current_phase as the comparison group.
    Falls back to all businesses if the cohort is too small.
    """
    phase = business.current_phase or 0

    # Get cohort business IDs (same phase, excluding self)
    cohort_query = select(Business.id).where(
        Business.id != business.id,
        Business.plan != "cancelled",
    )
    if phase > 0:
        cohort_query = cohort_query.where(Business.current_phase == phase)

    cohort_result = await session.execute(cohort_query)
    cohort_ids = [row[0] for row in cohort_result.all()]

    # If cohort is too small, expand to all active businesses
    if len(cohort_ids) < 3:
        all_result = await session.execute(
            select(Business.id).where(
                Business.id != business.id, Business.plan != "cancelled",
            )
        )
        cohort_ids = [row[0] for row in all_result.all()]

    if not cohort_ids:
        return {}

    metrics: dict[str, list] = {
        "content_total": [],
        "content_per_day": [],
        "cycle_completion_rate": [],
        "milestones_completed": [],
        "revenue_cents": [],
        "health_score": [],
    }

    # Batch-query cohort data
    for biz_id in cohort_ids:
        biz = await session.get(Business, biz_id)
        if not biz:
            continue

        content_count = (await session.execute(
            select(func.count(Content.id)).where(Content.business_id == biz_id)
        )).scalar() or 0

        day = max(getattr(biz, "roadmap_day", 1) or 1, 1)

        total_cycles = (await session.execute(
            select(func.count(Cycle.id)).where(Cycle.business_id == biz_id)
        )).scalar() or 0
        completed = (await session.execute(
            select(func.count(Cycle.id)).where(
                Cycle.business_id == biz_id, Cycle.status == "completed",
            )
        )).scalar() or 0

        milestones = (await session.execute(
            select(func.count(Milestone.id)).where(
                Milestone.business_id == biz_id, Milestone.status == "completed",
            )
        )).scalar() or 0

        revenue = (await session.execute(
            select(func.sum(RevenueEvent.amount_cents)).where(
                RevenueEvent.business_id == biz_id,
            )
        )).scalar() or 0

        metrics["content_total"].append(content_count)
        metrics["content_per_day"].append(round(content_count / day, 2))
        metrics["cycle_completion_rate"].append(round(completed / max(total_cycles, 1), 3))
        metrics["milestones_completed"].append(milestones)
        metrics["revenue_cents"].append(revenue)
        metrics["health_score"].append(getattr(biz, "health_score", 0) or 0)

    return metrics


def _calculate_percentile(value: float, cohort: list[float]) -> int:
    """Calculate which percentile a value falls in within the cohort."""
    if not cohort:
        return 50  # No comparison data
    sorted_cohort = sorted(cohort)
    below = sum(1 for v in sorted_cohort if v < value)
    equal = sum(1 for v in sorted_cohort if v == value)
    percentile = ((below + equal * 0.5) / len(sorted_cohort)) * 100
    return min(99, max(1, int(percentile)))


def _median(values: list[float]) -> float:
    """Calculate the median of a list."""
    if not values:
        return 0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 0:
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    return sorted_vals[n // 2]


def _assess_percentile(percentile: int) -> str:
    """Convert percentile to a human-readable assessment."""
    if percentile >= 80:
        return "excellent"
    if percentile >= 60:
        return "above_average"
    if percentile >= 40:
        return "average"
    if percentile >= 20:
        return "below_average"
    return "needs_improvement"
