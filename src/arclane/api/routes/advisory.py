"""Advisory API — notes, acknowledgment, weekly digest."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import AdvisoryNote, Business
from arclane.services.advisory_service import (
    check_warning_conditions,
    generate_weekly_digest,
)

router = APIRouter()


@router.get("/notes")
async def get_notes(
    acknowledged: bool | None = None,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get advisory notes, optionally filtered by acknowledged status."""
    query = select(AdvisoryNote).where(AdvisoryNote.business_id == business.id)
    if acknowledged is not None:
        query = query.where(AdvisoryNote.acknowledged == acknowledged)
    query = query.order_by(AdvisoryNote.priority.desc(), AdvisoryNote.created_at.desc()).limit(50)
    result = await session.execute(query)
    notes = result.scalars().all()
    return {
        "notes": [
            {
                "id": n.id,
                "category": n.category,
                "title": n.title,
                "body": n.body,
                "priority": n.priority,
                "acknowledged": n.acknowledged,
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ]
    }


@router.post("/notes/{note_id}/acknowledge")
async def acknowledge_note(
    note_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Acknowledge an advisory note."""
    note = await session.get(AdvisoryNote, note_id)
    if not note or note.business_id != business.id:
        raise HTTPException(status_code=404, detail="Note not found")
    note.acknowledged = True
    await session.commit()
    return {"status": "acknowledged", "note_id": note_id}


@router.get("/digest")
async def weekly_digest(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get the weekly digest summary."""
    return await generate_weekly_digest(business, session)


@router.get("/warnings")
async def get_warnings(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get active warning conditions."""
    return {"warnings": await check_warning_conditions(business, session)}
