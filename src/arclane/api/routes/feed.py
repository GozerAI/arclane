"""Activity feed — SSE stream + paginated history."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.schemas import ActivityEntry
from arclane.models.tables import Activity, Business

router = APIRouter()


@router.get("", response_model=list[ActivityEntry])
async def get_feed(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(50, le=200),
    before: datetime | None = None,
):
    query = select(Activity).where(Activity.business_id == business.id)
    if before:
        query = query.where(Activity.created_at < before)
    query = query.order_by(Activity.created_at.desc()).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/stream")
async def stream_feed(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """SSE stream of real-time activity for this business."""
    last_id = 0

    async def event_generator():
        nonlocal last_id
        while True:
            async with get_session().__class__(session.bind) as s:
                query = (
                    select(Activity)
                    .where(Activity.business_id == business.id)
                    .where(Activity.id > last_id)
                    .order_by(Activity.id.asc())
                    .limit(20)
                )
                result = await s.execute(query)
                entries = result.scalars().all()

            for entry in entries:
                last_id = entry.id
                yield {
                    "event": "activity",
                    "data": ActivityEntry.model_validate(entry).model_dump_json(),
                }

            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())
