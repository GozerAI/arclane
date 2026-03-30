"""Roadmap API — phase progression, milestones, next actions."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business
from arclane.services.roadmap_service import (
    advance_phase,
    check_phase_graduation,
    complete_milestone,
    get_next_actions,
    get_roadmap_summary,
)

router = APIRouter()


@router.get("")
async def get_roadmap(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get the full roadmap summary for a business."""
    return await get_roadmap_summary(business, session)


@router.get("/phase/{phase_number}")
async def get_phase(
    phase_number: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get details for a specific phase."""
    summary = await get_roadmap_summary(business, session)
    phase = next((p for p in summary["phases"] if p["phase_number"] == phase_number), None)
    if not phase:
        raise HTTPException(status_code=404, detail="Phase not found")
    return phase


@router.get("/milestones")
async def get_milestones(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get all milestones for a business."""
    summary = await get_roadmap_summary(business, session)
    milestones = []
    for phase in summary["phases"]:
        milestones.extend(phase["milestones"])
    return {"milestones": milestones, "total": len(milestones)}


@router.post("/milestones/{milestone_key}/complete")
async def mark_milestone_complete(
    milestone_key: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Manually mark a milestone as completed."""
    success = await complete_milestone(business, milestone_key, session, evidence={"source": "manual"})
    if not success:
        raise HTTPException(status_code=404, detail="Milestone not found")
    await session.commit()
    return {"status": "completed", "milestone_key": milestone_key}


@router.post("/advance")
async def advance_to_next_phase(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Attempt to advance to the next phase."""
    result = await advance_phase(business, session)
    await session.commit()
    if not result["advanced"]:
        return {"status": "not_ready", "detail": result["graduation_check"]}
    return {"status": "advanced", "from_phase": result["from_phase"], "to_phase": result["to_phase"]}


@router.get("/graduation")
async def check_graduation(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Check graduation readiness for the current phase."""
    return await check_phase_graduation(business, session)


@router.get("/next-actions")
async def get_recommended_actions(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get recommended next actions based on roadmap state."""
    return {"actions": await get_next_actions(business, session)}
