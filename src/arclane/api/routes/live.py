"""Public live feed — shows real-time activity across all businesses.

Like Polsia's /live page but powered by C-Suite's 16-executive engine.
No auth required — this is a marketing/transparency asset.
"""

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from arclane.api.app import limiter
from arclane.core.database import async_session, get_session
from arclane.core.logging import get_logger
from arclane.models.schemas import ActivityEntry
from arclane.models.tables import Activity, Business

log = get_logger("live")
router = APIRouter()


class LiveEntry(ActivityEntry):
    business_name: str
    business_slug: str


@router.get("")
@limiter.limit("60/minute")
async def get_live_feed(
    request: Request,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(50, le=200),
):
    """Get recent activity across all businesses."""
    result = await session.execute(
        select(Activity, Business.name, Business.slug)
        .join(Business, Activity.business_id == Business.id)
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        LiveEntry(
            id=act.id,
            action=act.action,
            detail=act.detail,
            created_at=act.created_at,
            business_name=biz_name,
            business_slug=biz_slug,
        )
        for act, biz_name, biz_slug in rows
    ]


@router.get("/stream")
async def stream_live_feed():
    """SSE stream of all activity across all businesses — public."""
    last_id = 0

    async def event_generator():
        nonlocal last_id
        while True:
            async with async_session() as session:
                result = await session.execute(
                    select(Activity, Business.name, Business.slug)
                    .join(Business, Activity.business_id == Business.id)
                    .where(Activity.id > last_id)
                    .order_by(Activity.id.asc())
                    .limit(20)
                )
                rows = result.all()

            for act, biz_name, biz_slug in rows:
                last_id = act.id
                entry = LiveEntry(
                    id=act.id,
                    action=act.action,
                    detail=act.detail,
                    created_at=act.created_at,
                    business_name=biz_name,
                    business_slug=biz_slug,
                )
                yield {
                    "event": "activity",
                    "data": entry.model_dump_json(),
                }

            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())


@router.get("/stats")
async def live_stats(
    session: AsyncSession = Depends(get_session),
):
    """Public stats — how many businesses, cycles, content pieces."""
    from sqlalchemy import func
    from arclane.models.tables import Content, Cycle

    biz_count = (await session.execute(select(func.count(Business.id)))).scalar() or 0
    cycle_count = (await session.execute(select(func.count(Cycle.id)))).scalar() or 0
    content_count = (await session.execute(select(func.count(Content.id)))).scalar() or 0
    activity_count = (await session.execute(select(func.count(Activity.id)))).scalar() or 0

    return {
        "businesses": biz_count,
        "cycles_completed": cycle_count,
        "content_produced": content_count,
        "total_actions": activity_count,
    }
