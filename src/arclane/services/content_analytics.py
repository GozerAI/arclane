"""Content performance analytics — tracks and analyzes content effectiveness."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import Business, Content, ContentPerformance

log = get_logger("content_analytics")


async def record_performance(
    content_id: int,
    session: AsyncSession,
    *,
    metric_name: str,
    value: float,
    source: str = "manual",
) -> ContentPerformance:
    """Record a performance metric for a content item."""
    record = ContentPerformance(
        content_id=content_id,
        metric_name=metric_name,
        value=value,
        source=source,
    )
    session.add(record)
    await session.flush()
    return record


async def get_content_performance(content_id: int, session: AsyncSession) -> dict:
    """Get all performance metrics for a content item."""
    result = await session.execute(
        select(ContentPerformance)
        .where(ContentPerformance.content_id == content_id)
        .order_by(ContentPerformance.recorded_at.desc())
    )
    records = result.scalars().all()

    metrics = {}
    for r in records:
        if r.metric_name not in metrics:
            metrics[r.metric_name] = {
                "latest_value": r.value,
                "source": r.source,
                "recorded_at": r.recorded_at.isoformat(),
            }

    return {"content_id": content_id, "metrics": metrics}


async def get_top_performing_content(
    business: Business,
    session: AsyncSession,
    metric: str = "views",
    limit: int = 10,
) -> list[dict]:
    """Get the top performing content items for a business by a given metric."""
    result = await session.execute(
        select(Content, func.max(ContentPerformance.value).label("max_value"))
        .join(ContentPerformance, ContentPerformance.content_id == Content.id)
        .where(
            Content.business_id == business.id,
            ContentPerformance.metric_name == metric,
        )
        .group_by(Content.id)
        .order_by(func.max(ContentPerformance.value).desc())
        .limit(limit)
    )

    return [
        {
            "content_id": content.id,
            "title": content.title,
            "content_type": content.content_type,
            "value": max_val,
            "metric": metric,
        }
        for content, max_val in result.all()
    ]


async def get_performance_by_type(business: Business, session: AsyncSession) -> dict:
    """Get average performance metrics grouped by content type."""
    result = await session.execute(
        select(
            Content.content_type,
            ContentPerformance.metric_name,
            func.avg(ContentPerformance.value),
            func.count(ContentPerformance.id),
        )
        .join(ContentPerformance, ContentPerformance.content_id == Content.id)
        .where(Content.business_id == business.id)
        .group_by(Content.content_type, ContentPerformance.metric_name)
    )

    type_metrics: dict = {}
    for content_type, metric_name, avg_val, count in result.all():
        if content_type not in type_metrics:
            type_metrics[content_type] = {}
        type_metrics[content_type][metric_name] = {
            "average": round(avg_val, 2),
            "sample_size": count,
        }

    return type_metrics


async def get_content_insights(business: Business, session: AsyncSession) -> list[dict]:
    """Generate insights about content performance patterns."""
    insights = []

    type_metrics = await get_performance_by_type(business, session)

    if not type_metrics:
        return [{"insight": "No performance data yet. Publish content and track metrics to see insights."}]

    # Find best performing content type
    best_type = None
    best_avg = 0
    for content_type, metrics in type_metrics.items():
        views = metrics.get("views", {}).get("average", 0)
        if views > best_avg:
            best_avg = views
            best_type = content_type

    if best_type:
        insights.append({
            "insight": f"Your {best_type} content performs best with an average of {best_avg:.0f} views.",
            "recommendation": f"Produce more {best_type} content to capitalize on this strength.",
            "metric": "views",
            "content_type": best_type,
        })

    # Check for underperformers
    for content_type, metrics in type_metrics.items():
        views = metrics.get("views", {}).get("average", 0)
        if views < best_avg * 0.3 and best_avg > 0:
            insights.append({
                "insight": f"Your {content_type} content averages only {views:.0f} views ({views/best_avg*100:.0f}% of your best type).",
                "recommendation": f"Consider adjusting your {content_type} strategy or reallocating effort to {best_type}.",
                "metric": "views",
                "content_type": content_type,
            })

    return insights
