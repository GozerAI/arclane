"""Business metrics."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.schemas import MetricEntry
from arclane.models.tables import Business, Metric

router = APIRouter()


@router.get("", response_model=list[MetricEntry])
async def list_metrics(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
    name: str | None = None,
    since: datetime | None = None,
    limit: int = Query(100, le=500),
):
    query = select(Metric).where(Metric.business_id == business.id)
    if name:
        query = query.where(Metric.name == name)
    if since:
        query = query.where(Metric.recorded_at >= since)
    query = query.order_by(Metric.recorded_at.desc()).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()
