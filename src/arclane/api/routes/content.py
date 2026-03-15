"""Content produced by agents."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.schemas import (
    ContentEntry,
    ContentUpdateRequest,
    VALID_CONTENT_STATUSES,
    VALID_CONTENT_TYPES,
)
from arclane.models.tables import Business, Content, Metric

router = APIRouter()


@router.get("", response_model=list[ContentEntry])
async def list_content(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
    content_type: str | None = None,
    status: str | None = None,
    limit: int = Query(50, le=200),
):
    if content_type and content_type not in VALID_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid content type")
    if status and status not in VALID_CONTENT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    query = select(Content).where(Content.business_id == business.id)
    if content_type:
        query = query.where(Content.content_type == content_type)
    if status:
        query = query.where(Content.status == status)
    query = query.order_by(Content.created_at.desc()).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{content_id}", response_model=ContentEntry)
async def get_content(
    content_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Content)
        .where(Content.id == content_id)
        .where(Content.business_id == business.id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Content not found")
    return entry


@router.patch("/{content_id}", response_model=ContentEntry)
async def update_content(
    content_id: int,
    payload: ContentUpdateRequest,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Content)
        .where(Content.id == content_id)
        .where(Content.business_id == business.id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Content not found")

    entry.status = payload.status
    if payload.status == "published":
        entry.published_at = entry.published_at or datetime.now(timezone.utc)
    elif payload.status != "scheduled":
        entry.published_at = None

    total_content = (
        await session.execute(
            select(func.count(Content.id)).where(Content.business_id == business.id)
        )
    ).scalar() or 0
    published_content = (
        await session.execute(
            select(func.count(Content.id))
            .where(Content.business_id == business.id)
            .where(Content.status == "published")
        )
    ).scalar() or 0

    session.add(Metric(business_id=business.id, name="content_total", value=float(total_content)))
    session.add(Metric(business_id=business.id, name="content_published", value=float(published_content)))
    await session.commit()
    await session.refresh(entry)
    return entry
