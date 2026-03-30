"""Revenue tracking — events, ROI, attribution, summaries."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import Business, RevenueEvent

log = get_logger("revenue_tracker")


async def record_revenue_event(
    business: Business,
    session: AsyncSession,
    *,
    source: str,
    amount_cents: int,
    currency: str = "usd",
    attribution: dict | None = None,
    event_date: datetime | None = None,
) -> RevenueEvent:
    """Record a revenue event for a business."""
    event = RevenueEvent(
        business_id=business.id,
        source=source,
        amount_cents=amount_cents,
        currency=currency,
        attribution_json=attribution,
        event_date=event_date or datetime.now(timezone.utc),
    )
    session.add(event)
    await session.flush()
    log.info("Revenue event recorded for %s: %d cents from %s", business.slug, amount_cents, source)
    return event


async def get_revenue_summary(business: Business, session: AsyncSession) -> dict:
    """Return revenue summary with totals and breakdowns."""
    # Total revenue
    total_result = await session.execute(
        select(func.sum(RevenueEvent.amount_cents), func.count(RevenueEvent.id))
        .where(RevenueEvent.business_id == business.id)
    )
    row = total_result.one()
    total_cents = row[0] or 0
    total_events = row[1] or 0

    # Revenue by source
    source_result = await session.execute(
        select(RevenueEvent.source, func.sum(RevenueEvent.amount_cents), func.count(RevenueEvent.id))
        .where(RevenueEvent.business_id == business.id)
        .group_by(RevenueEvent.source)
    )
    by_source = {row[0]: {"total_cents": row[1], "events": row[2]} for row in source_result.all()}

    # Monthly breakdown (last 3 months)
    three_months_ago = datetime.now(timezone.utc) - timedelta(days=90)
    monthly_result = await session.execute(
        select(
            func.strftime("%Y-%m", RevenueEvent.event_date),
            func.sum(RevenueEvent.amount_cents),
            func.count(RevenueEvent.id),
        )
        .where(
            RevenueEvent.business_id == business.id,
            RevenueEvent.event_date >= three_months_ago,
        )
        .group_by(func.strftime("%Y-%m", RevenueEvent.event_date))
        .order_by(func.strftime("%Y-%m", RevenueEvent.event_date))
    )
    monthly = [
        {"month": row[0], "total_cents": row[1], "events": row[2]}
        for row in monthly_result.all()
    ]

    return {
        "total_cents": total_cents,
        "total_usd": total_cents / 100,
        "total_events": total_events,
        "by_source": by_source,
        "monthly": monthly,
    }


async def calculate_roi(business: Business, session: AsyncSession) -> dict:
    """Calculate ROI based on revenue vs subscription cost."""

    total_result = await session.execute(
        select(func.sum(RevenueEvent.amount_cents))
        .where(RevenueEvent.business_id == business.id)
    )
    total_revenue_cents = total_result.scalar() or 0

    # Estimate subscription cost based on plan and time
    plan_monthly_cents = PLAN_PRICES.get(business.plan, 0) * 100
    months_active = max(1, ((business.roadmap_day or 0) + 29) // 30)
    total_cost_cents = plan_monthly_cents * months_active

    roi_pct = ((total_revenue_cents - total_cost_cents) / max(total_cost_cents, 1)) * 100 if total_cost_cents > 0 else 0

    return {
        "total_revenue_cents": total_revenue_cents,
        "total_revenue_usd": total_revenue_cents / 100,
        "estimated_cost_cents": total_cost_cents,
        "estimated_cost_usd": total_cost_cents / 100,
        "roi_pct": round(roi_pct, 1),
        "months_active": months_active,
        "plan": business.plan,
    }


async def get_attribution_summary(business: Business, session: AsyncSession) -> list[dict]:
    """Return revenue attribution breakdown."""
    result = await session.execute(
        select(RevenueEvent)
        .where(
            RevenueEvent.business_id == business.id,
            RevenueEvent.attribution_json != None,  # noqa: E711
        )
        .order_by(RevenueEvent.event_date.desc())
        .limit(100)
    )
    events = result.scalars().all()

    # Aggregate by UTM source
    source_totals: dict[str, int] = {}
    for event in events:
        attr = event.attribution_json or {}
        utm_source = attr.get("utm_source", "direct")
        source_totals[utm_source] = source_totals.get(utm_source, 0) + event.amount_cents

    return [
        {"source": source, "total_cents": cents, "total_usd": cents / 100}
        for source, cents in sorted(source_totals.items(), key=lambda x: x[1], reverse=True)
    ]
