"""Advanced analytics routes — Pro plan and above."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import (
    Activity,
    Business,
    BusinessHealthScore,
    Content,
    ContentPerformance,
    Cycle,
    Milestone,
    RevenueEvent,
)

log = get_logger("advanced_analytics")
router = APIRouter()

ADVANCED_PLANS = {"pro", "growth", "scale", "enterprise"}


def _require_pro_plan(business: Business) -> None:
    if business.plan not in ADVANCED_PLANS:
        raise HTTPException(
            status_code=403,
            detail="Advanced analytics requires Pro plan or above",
        )


# --- Health trend ---


class HealthTrendPoint(BaseModel):
    score: float
    score_type: str
    recorded_at: str


@router.get("/trend", response_model=list[HealthTrendPoint])
async def health_trend(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
    days: int = Query(30, ge=1, le=90),
):
    """Health score trend over time."""
    _require_pro_plan(business)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(BusinessHealthScore)
        .where(
            BusinessHealthScore.business_id == business.id,
            BusinessHealthScore.recorded_at >= cutoff,
        )
        .order_by(BusinessHealthScore.recorded_at.asc())
    )
    scores = result.scalars().all()
    return [
        HealthTrendPoint(
            score=s.score,
            score_type=s.score_type,
            recorded_at=s.recorded_at.isoformat(),
        )
        for s in scores
    ]


# --- Content ROI ---


class ContentROIResponse(BaseModel):
    total_content: int
    content_with_revenue: int
    total_revenue_cents: int
    avg_revenue_per_content_cents: float
    top_performing: list[dict]


@router.get("/content-roi", response_model=ContentROIResponse)
async def content_roi(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Content performance correlated with revenue."""
    _require_pro_plan(business)

    total_content = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id == business.id)
    )).scalar() or 0

    # Get revenue events with content attribution
    rev_result = await session.execute(
        select(RevenueEvent)
        .where(RevenueEvent.business_id == business.id)
        .order_by(RevenueEvent.amount_cents.desc())
    )
    events = rev_result.scalars().all()

    total_rev = sum(e.amount_cents for e in events)
    attributed = [e for e in events if e.attribution_json and e.attribution_json.get("content_id")]

    # Top performing content by views
    perf_result = await session.execute(
        select(
            ContentPerformance.content_id,
            func.sum(ContentPerformance.value).label("total"),
        )
        .where(ContentPerformance.metric_name == "views")
        .join(Content, Content.id == ContentPerformance.content_id)
        .where(Content.business_id == business.id)
        .group_by(ContentPerformance.content_id)
        .order_by(func.sum(ContentPerformance.value).desc())
        .limit(5)
    )
    top = [{"content_id": r[0], "total_views": r[1]} for r in perf_result.all()]

    return ContentROIResponse(
        total_content=total_content,
        content_with_revenue=len(attributed),
        total_revenue_cents=total_rev,
        avg_revenue_per_content_cents=round(total_rev / max(total_content, 1), 2),
        top_performing=top,
    )


# --- Cycle efficiency ---


class CycleEfficiencyResponse(BaseModel):
    total_cycles: int
    completed: int
    failed: int
    success_rate: float
    avg_tasks_per_cycle: float
    cycles_by_month: dict[str, int]


@router.get("/cycle-efficiency", response_model=CycleEfficiencyResponse)
async def cycle_efficiency(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
    months: int = Query(3, ge=1, le=12),
):
    """Cycle success rate and efficiency over time."""
    _require_pro_plan(business)
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)

    result = await session.execute(
        select(Cycle).where(
            Cycle.business_id == business.id,
            Cycle.created_at >= cutoff,
        )
    )
    cycles = result.scalars().all()

    completed = sum(1 for c in cycles if c.status == "completed")
    failed = sum(1 for c in cycles if c.status == "failed")
    total = len(cycles)

    # Tasks per cycle from result JSON
    task_counts = []
    for c in cycles:
        if c.result and isinstance(c.result, dict):
            tasks = c.result.get("tasks", [])
            task_counts.append(len(tasks) if isinstance(tasks, list) else 0)

    # Group by month
    by_month: dict[str, int] = {}
    for c in cycles:
        key = c.created_at.strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0) + 1

    return CycleEfficiencyResponse(
        total_cycles=total,
        completed=completed,
        failed=failed,
        success_rate=round((completed / max(total, 1)) * 100, 1),
        avg_tasks_per_cycle=round(sum(task_counts) / max(len(task_counts), 1), 1),
        cycles_by_month=by_month,
    )


# --- Growth metrics ---


class GrowthMetricsResponse(BaseModel):
    content_growth_rate: float  # month-over-month %
    milestone_velocity: float  # milestones per week
    revenue_growth_rate: float  # month-over-month %
    total_milestones_completed: int
    total_milestones: int


@router.get("/growth-metrics", response_model=GrowthMetricsResponse)
async def growth_metrics(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Month-over-month growth metrics."""
    _require_pro_plan(business)
    now = datetime.now(timezone.utc)
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)

    # Content growth MoM
    this_month_content = (await session.execute(
        select(func.count(Content.id)).where(
            Content.business_id == business.id,
            Content.created_at >= this_month_start,
        )
    )).scalar() or 0

    last_month_content = (await session.execute(
        select(func.count(Content.id)).where(
            Content.business_id == business.id,
            Content.created_at >= last_month_start,
            Content.created_at < this_month_start,
        )
    )).scalar() or 0

    content_growth = 0.0
    if last_month_content > 0:
        content_growth = round(((this_month_content - last_month_content) / last_month_content) * 100, 1)

    # Revenue growth MoM
    this_month_rev = (await session.execute(
        select(func.coalesce(func.sum(RevenueEvent.amount_cents), 0)).where(
            RevenueEvent.business_id == business.id,
            RevenueEvent.event_date >= this_month_start,
        )
    )).scalar() or 0

    last_month_rev = (await session.execute(
        select(func.coalesce(func.sum(RevenueEvent.amount_cents), 0)).where(
            RevenueEvent.business_id == business.id,
            RevenueEvent.event_date >= last_month_start,
            RevenueEvent.event_date < this_month_start,
        )
    )).scalar() or 0

    rev_growth = 0.0
    if last_month_rev > 0:
        rev_growth = round(((this_month_rev - last_month_rev) / last_month_rev) * 100, 1)

    # Milestones
    total_ms = (await session.execute(
        select(func.count(Milestone.id)).where(Milestone.business_id == business.id)
    )).scalar() or 0

    completed_ms = (await session.execute(
        select(func.count(Milestone.id)).where(
            Milestone.business_id == business.id,
            Milestone.status == "completed",
        )
    )).scalar() or 0

    # Velocity: completed milestones in last 14 days / 2
    two_weeks_ago = now - timedelta(days=14)
    recent_ms = (await session.execute(
        select(func.count(Milestone.id)).where(
            Milestone.business_id == business.id,
            Milestone.status == "completed",
            Milestone.completed_at >= two_weeks_ago,
        )
    )).scalar() or 0
    velocity = round(recent_ms / 2, 1)

    return GrowthMetricsResponse(
        content_growth_rate=content_growth,
        milestone_velocity=velocity,
        revenue_growth_rate=rev_growth,
        total_milestones_completed=completed_ms,
        total_milestones=total_ms,
    )


# --- Engagement heatmap ---


class EngagementHeatmapResponse(BaseModel):
    by_hour: dict[int, int]
    by_day_of_week: dict[str, int]
    peak_hour: int
    peak_day: str


@router.get("/engagement-heatmap", response_model=EngagementHeatmapResponse)
async def engagement_heatmap(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
    days: int = Query(30, ge=7, le=90),
):
    """Activity distribution by hour and day of week."""
    _require_pro_plan(business)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await session.execute(
        select(Activity.created_at).where(
            Activity.business_id == business.id,
            Activity.created_at >= cutoff,
        )
    )
    timestamps = [row[0] for row in result.all()]

    by_hour: dict[int, int] = {h: 0 for h in range(24)}
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_day: dict[str, int] = {d: 0 for d in day_names}

    for ts in timestamps:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        by_hour[ts.hour] = by_hour.get(ts.hour, 0) + 1
        by_day[day_names[ts.weekday()]] = by_day.get(day_names[ts.weekday()], 0) + 1

    peak_hour = max(by_hour, key=by_hour.get) if timestamps else 0
    peak_day = max(by_day, key=by_day.get) if timestamps else "Monday"

    return EngagementHeatmapResponse(
        by_hour=by_hour,
        by_day_of_week=by_day,
        peak_hour=peak_hour,
        peak_day=peak_day,
    )
