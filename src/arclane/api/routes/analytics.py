"""Analytics routes — customer insights, cohort metrics, business health score."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Activity, Business, Content, Cycle, Metric

router = APIRouter()


# --- Business health score ---


class BusinessHealthResponse(BaseModel):
    business_id: int
    slug: str
    health_score: float
    total_cycles: int
    completed_cycles: int
    failed_cycles: int
    success_rate: float
    total_content: int
    published_content: int
    working_days_remaining: int
    days_since_last_cycle: float | None
    assessment: str


@router.get("/health", response_model=BusinessHealthResponse)
async def business_health(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Compute a health score for the business based on cycle success, content
    production, working day status, and recency of activity."""
    total_cycles = (await session.execute(
        select(func.count(Cycle.id)).where(Cycle.business_id == business.id)
    )).scalar() or 0

    completed = (await session.execute(
        select(func.count(Cycle.id)).where(
            Cycle.business_id == business.id, Cycle.status == "completed",
        )
    )).scalar() or 0

    failed = (await session.execute(
        select(func.count(Cycle.id)).where(
            Cycle.business_id == business.id, Cycle.status == "failed",
        )
    )).scalar() or 0

    total_content = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id == business.id)
    )).scalar() or 0

    published = (await session.execute(
        select(func.count(Content.id)).where(
            Content.business_id == business.id, Content.status == "published",
        )
    )).scalar() or 0

    last_cycle_at = (await session.execute(
        select(Cycle.completed_at).where(
            Cycle.business_id == business.id,
        ).order_by(Cycle.created_at.desc()).limit(1)
    )).scalar()

    now = datetime.now(timezone.utc)
    days_since = None
    if last_cycle_at:
        lc = last_cycle_at if last_cycle_at.tzinfo else last_cycle_at.replace(tzinfo=timezone.utc)
        days_since = round((now - lc).total_seconds() / 86400, 1)

    # Compute score (0-100)
    success_rate = (completed / max(total_cycles, 1)) * 100
    score = 0.0

    # Success rate: up to 40 points
    score += min(40.0, success_rate * 0.4)

    # Content production: up to 20 points
    score += min(20.0, total_content * 2.0)

    # Credit health: up to 20 points
    total_working_days = business.working_days_remaining + business.working_days_bonus
    if total_working_days >= 10:
        score += 20.0
    elif total_working_days >= 5:
        score += 15.0
    elif total_working_days >= 2:
        score += 10.0
    elif total_working_days >= 1:
        score += 5.0

    # Recency: up to 20 points
    if days_since is not None:
        if days_since <= 1:
            score += 20.0
        elif days_since <= 3:
            score += 15.0
        elif days_since <= 7:
            score += 10.0
        elif days_since <= 14:
            score += 5.0

    score = min(100.0, round(score, 1))

    if score >= 80:
        assessment = "Thriving — strong execution and content momentum"
    elif score >= 60:
        assessment = "Healthy — steady progress with room to accelerate"
    elif score >= 40:
        assessment = "Needs attention — cycle success or content output is lagging"
    elif score >= 20:
        assessment = "At risk — low activity or frequent failures"
    else:
        assessment = "Stalled — no meaningful activity detected"

    return BusinessHealthResponse(
        business_id=business.id, slug=business.slug,
        health_score=score, total_cycles=total_cycles,
        completed_cycles=completed, failed_cycles=failed,
        success_rate=round(success_rate, 1),
        total_content=total_content, published_content=published,
        working_days_remaining=business.working_days_remaining + business.working_days_bonus,
        days_since_last_cycle=days_since,
        assessment=assessment,
    )


# --- Cohort summary ---


class CohortEntry(BaseModel):
    month: str
    businesses_created: int
    total_cycles: int
    total_content: int
    avg_working_days_remaining: float


@router.get("/cohorts", response_model=list[CohortEntry])
async def cohort_summary(
    months: int = Query(6, ge=1, le=24),
    session: AsyncSession = Depends(get_session),
):
    """Monthly cohort breakdown of business creation and activity."""
    now = datetime.now(timezone.utc)
    results = []

    for i in range(months):
        month_start = (now.replace(day=1) - timedelta(days=30 * i)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        if i == 0:
            month_end = now
        else:
            next_month = (month_start + timedelta(days=32)).replace(day=1)
            month_end = next_month

        biz_count = (await session.execute(
            select(func.count(Business.id)).where(
                Business.created_at >= month_start,
                Business.created_at < month_end,
            )
        )).scalar() or 0

        cycle_count = (await session.execute(
            select(func.count(Cycle.id)).where(
                Cycle.created_at >= month_start,
                Cycle.created_at < month_end,
            )
        )).scalar() or 0

        content_count = (await session.execute(
            select(func.count(Content.id)).where(
                Content.created_at >= month_start,
                Content.created_at < month_end,
            )
        )).scalar() or 0

        avg_working_days = (await session.execute(
            select(func.avg(Business.working_days_remaining)).where(
                Business.created_at >= month_start,
                Business.created_at < month_end,
            )
        )).scalar() or 0.0

        results.append(CohortEntry(
            month=month_start.strftime("%Y-%m"),
            businesses_created=biz_count,
            total_cycles=cycle_count,
            total_content=content_count,
            avg_working_days_remaining=round(float(avg_working_days), 1),
        ))

    return list(reversed(results))


# --- Activity summary ---


class ActivitySummaryResponse(BaseModel):
    total_activities: int
    last_24h: int
    last_7d: int
    top_actions: dict[str, int]


@router.get("/activity-summary", response_model=ActivitySummaryResponse)
async def activity_summary(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Summarize activity for a business."""
    now = datetime.now(timezone.utc)

    total = (await session.execute(
        select(func.count(Activity.id)).where(Activity.business_id == business.id)
    )).scalar() or 0

    last_24h = (await session.execute(
        select(func.count(Activity.id)).where(
            Activity.business_id == business.id,
            Activity.created_at >= now - timedelta(hours=24),
        )
    )).scalar() or 0

    last_7d = (await session.execute(
        select(func.count(Activity.id)).where(
            Activity.business_id == business.id,
            Activity.created_at >= now - timedelta(days=7),
        )
    )).scalar() or 0

    # Top actions
    action_result = await session.execute(
        select(Activity.action, func.count(Activity.id))
        .where(Activity.business_id == business.id)
        .group_by(Activity.action)
        .order_by(func.count(Activity.id).desc())
        .limit(10)
    )
    top_actions = {row[0]: row[1] for row in action_result.all()}

    return ActivitySummaryResponse(
        total_activities=total, last_24h=last_24h,
        last_7d=last_7d, top_actions=top_actions,
    )
