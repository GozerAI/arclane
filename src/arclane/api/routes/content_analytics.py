"""Content analytics API — performance tracking and insights."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business, Content
from arclane.services.content_analytics import (
    get_content_insights,
    get_content_performance,
    get_performance_by_type,
    get_top_performing_content,
    record_performance,
)

router = APIRouter()


class PerformanceRecord(BaseModel):
    metric_name: str = Field(..., max_length=100)
    value: float
    source: str = Field("manual", max_length=100)


@router.post("/{content_id}/performance")
async def record_content_performance(
    content_id: int,
    payload: PerformanceRecord,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Record a performance metric for a content item."""
    content = await session.get(Content, content_id)
    if not content or content.business_id != business.id:
        raise HTTPException(status_code=404, detail="Content not found")

    record = await record_performance(
        content_id, session,
        metric_name=payload.metric_name,
        value=payload.value,
        source=payload.source,
    )
    await session.commit()
    return {"id": record.id, "metric_name": record.metric_name, "value": record.value}


@router.get("/{content_id}/performance")
async def get_performance(
    content_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get performance metrics for a content item."""
    content = await session.get(Content, content_id)
    if not content or content.business_id != business.id:
        raise HTTPException(status_code=404, detail="Content not found")
    return await get_content_performance(content_id, session)


@router.get("/top")
async def top_content(
    metric: str = "views",
    limit: int = 10,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get top performing content by a metric."""
    return {"top": await get_top_performing_content(business, session, metric=metric, limit=limit)}


@router.get("/by-type")
async def performance_by_type(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get average performance grouped by content type."""
    return {"by_type": await get_performance_by_type(business, session)}


@router.get("/insights")
async def content_insights(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get AI-generated insights about content performance patterns."""
    return {"insights": await get_content_insights(business, session)}
