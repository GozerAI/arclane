"""Content produced by agents."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.schemas import ContentEntry, VALID_CONTENT_TYPES, VALID_CONTENT_STATUSES
from arclane.models.tables import Business, Content

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
