"""Portfolio dashboard routes — Growth plan and above."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.auth import get_current_user_email, _auth_required
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import Business, Content, Cycle, RevenueEvent

log = get_logger("portfolio")
router = APIRouter()

PORTFOLIO_PLANS = {"growth", "scale", "enterprise"}


def _get_email_or_401(request: Request) -> str:
    email = get_current_user_email(request)
    if not email:
        if _auth_required:
            raise HTTPException(status_code=401, detail="Authentication required")
        raise HTTPException(status_code=401, detail="Authentication required for portfolio")
    return email


async def _get_user_businesses(email: str, session: AsyncSession) -> list[Business]:
    result = await session.execute(
        select(Business).where(
            Business.owner_email == email,
            Business.plan != "cancelled",
        )
    )
    return list(result.scalars().all())


def _require_portfolio_plan(businesses: list[Business]) -> None:
    """Check if any of the user's businesses is on a portfolio-eligible plan."""
    if not any(b.plan in PORTFOLIO_PLANS for b in businesses):
        raise HTTPException(
            status_code=403,
            detail="Portfolio dashboard requires Growth plan or above",
        )


# --- Overview ---


class PortfolioBusinessSummary(BaseModel):
    slug: str
    name: str
    plan: str
    health_score: float | None
    working_days_remaining: int
    current_phase: int
    roadmap_day: int


class PortfolioOverview(BaseModel):
    total_businesses: int
    businesses: list[PortfolioBusinessSummary]
    total_working_days: int
    avg_health_score: float | None
    total_revenue_cents: int
    total_content: int
    total_cycles: int


@router.get("/overview", response_model=PortfolioOverview)
async def portfolio_overview(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Aggregated view across all businesses owned by the user."""
    email = _get_email_or_401(request)
    businesses = await _get_user_businesses(email, session)
    _require_portfolio_plan(businesses)

    biz_ids = [b.id for b in businesses]

    total_content = 0
    total_cycles = 0
    total_revenue = 0

    if biz_ids:
        total_content = (await session.execute(
            select(func.count(Content.id)).where(Content.business_id.in_(biz_ids))
        )).scalar() or 0

        total_cycles = (await session.execute(
            select(func.count(Cycle.id)).where(Cycle.business_id.in_(biz_ids))
        )).scalar() or 0

        total_revenue = (await session.execute(
            select(func.coalesce(func.sum(RevenueEvent.amount_cents), 0))
            .where(RevenueEvent.business_id.in_(biz_ids))
        )).scalar() or 0

    health_scores = [b.health_score for b in businesses if b.health_score is not None]
    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else None

    summaries = [
        PortfolioBusinessSummary(
            slug=b.slug, name=b.name, plan=b.plan,
            health_score=b.health_score,
            working_days_remaining=b.working_days_remaining + b.working_days_bonus,
            current_phase=b.current_phase or 0,
            roadmap_day=b.roadmap_day or 0,
        )
        for b in businesses
        if not b.slug.startswith("_user-")  # Exclude user stubs
    ]

    return PortfolioOverview(
        total_businesses=len(summaries),
        businesses=summaries,
        total_working_days=sum(b.working_days_remaining + b.working_days_bonus for b in businesses),
        avg_health_score=avg_health,
        total_revenue_cents=total_revenue,
        total_content=total_content,
        total_cycles=total_cycles,
    )


# --- Health across businesses ---


class PortfolioHealthEntry(BaseModel):
    slug: str
    name: str
    health_score: float | None
    current_phase: int
    roadmap_day: int


@router.get("/health", response_model=list[PortfolioHealthEntry])
async def portfolio_health(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Health scores across all businesses."""
    email = _get_email_or_401(request)
    businesses = await _get_user_businesses(email, session)
    _require_portfolio_plan(businesses)

    return [
        PortfolioHealthEntry(
            slug=b.slug, name=b.name,
            health_score=b.health_score,
            current_phase=b.current_phase or 0,
            roadmap_day=b.roadmap_day or 0,
        )
        for b in businesses
        if not b.slug.startswith("_user-")
    ]


# --- Content summary ---


class PortfolioContentSummary(BaseModel):
    total_content: int
    by_type: dict[str, int]
    by_business: dict[str, int]


@router.get("/content-summary", response_model=PortfolioContentSummary)
async def portfolio_content_summary(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Content counts aggregated across all businesses."""
    email = _get_email_or_401(request)
    businesses = await _get_user_businesses(email, session)
    _require_portfolio_plan(businesses)

    biz_ids = [b.id for b in businesses]
    biz_map = {b.id: b.slug for b in businesses}

    if not biz_ids:
        return PortfolioContentSummary(total_content=0, by_type={}, by_business={})

    total = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id.in_(biz_ids))
    )).scalar() or 0

    by_type_result = await session.execute(
        select(Content.content_type, func.count(Content.id))
        .where(Content.business_id.in_(biz_ids))
        .group_by(Content.content_type)
    )
    by_type = {row[0]: row[1] for row in by_type_result.all()}

    by_biz_result = await session.execute(
        select(Content.business_id, func.count(Content.id))
        .where(Content.business_id.in_(biz_ids))
        .group_by(Content.business_id)
    )
    by_business = {biz_map.get(row[0], str(row[0])): row[1] for row in by_biz_result.all()}

    return PortfolioContentSummary(
        total_content=total, by_type=by_type, by_business=by_business,
    )


# --- Revenue summary ---


class PortfolioRevenueSummary(BaseModel):
    total_revenue_cents: int
    by_business: dict[str, int]
    by_source: dict[str, int]


@router.get("/revenue-summary", response_model=PortfolioRevenueSummary)
async def portfolio_revenue_summary(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Revenue aggregation across all businesses."""
    email = _get_email_or_401(request)
    businesses = await _get_user_businesses(email, session)
    _require_portfolio_plan(businesses)

    biz_ids = [b.id for b in businesses]
    biz_map = {b.id: b.slug for b in businesses}

    if not biz_ids:
        return PortfolioRevenueSummary(
            total_revenue_cents=0, by_business={}, by_source={},
        )

    total = (await session.execute(
        select(func.coalesce(func.sum(RevenueEvent.amount_cents), 0))
        .where(RevenueEvent.business_id.in_(biz_ids))
    )).scalar() or 0

    by_biz_result = await session.execute(
        select(RevenueEvent.business_id, func.sum(RevenueEvent.amount_cents))
        .where(RevenueEvent.business_id.in_(biz_ids))
        .group_by(RevenueEvent.business_id)
    )
    by_business = {biz_map.get(row[0], str(row[0])): row[1] for row in by_biz_result.all()}

    by_src_result = await session.execute(
        select(RevenueEvent.source, func.sum(RevenueEvent.amount_cents))
        .where(RevenueEvent.business_id.in_(biz_ids))
        .group_by(RevenueEvent.source)
    )
    by_source = {row[0]: row[1] for row in by_src_result.all()}

    return PortfolioRevenueSummary(
        total_revenue_cents=total, by_business=by_business, by_source=by_source,
    )


# --- Cycle status ---


class PortfolioCycleStatus(BaseModel):
    slug: str
    name: str
    last_cycle_status: str | None
    last_cycle_at: str | None
    total_cycles: int


@router.get("/cycle-status", response_model=list[PortfolioCycleStatus])
async def portfolio_cycle_status(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Last cycle status per business."""
    email = _get_email_or_401(request)
    businesses = await _get_user_businesses(email, session)
    _require_portfolio_plan(businesses)

    results = []
    for biz in businesses:
        if biz.slug.startswith("_user-"):
            continue

        total = (await session.execute(
            select(func.count(Cycle.id)).where(Cycle.business_id == biz.id)
        )).scalar() or 0

        last = (await session.execute(
            select(Cycle)
            .where(Cycle.business_id == biz.id)
            .order_by(Cycle.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        results.append(PortfolioCycleStatus(
            slug=biz.slug, name=biz.name,
            last_cycle_status=last.status if last else None,
            last_cycle_at=last.created_at.isoformat() if last else None,
            total_cycles=total,
        ))

    return results
