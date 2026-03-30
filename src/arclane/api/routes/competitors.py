"""Competitors API — tracking and competitive intelligence."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business
from arclane.services.competitive_monitor import (
    add_competitor,
    get_competitive_brief,
    get_competitors,
    run_check,
)

router = APIRouter()


class CompetitorCreate(BaseModel):
    name: str = Field(..., max_length=255)
    url: str | None = Field(None, max_length=500)


@router.post("")
async def add_new_competitor(
    payload: CompetitorCreate,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Add a competitor to monitor."""
    monitor = await add_competitor(business, session, name=payload.name, url=payload.url)
    await session.commit()
    return {"id": monitor.id, "name": monitor.competitor_name, "url": monitor.competitor_url}


@router.get("")
async def list_competitors(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List all monitored competitors."""
    return {"competitors": await get_competitors(business, session)}


@router.post("/check")
async def trigger_check(
    competitor_id: int | None = None,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Run a competitive check."""
    results = await run_check(business, session, competitor_id=competitor_id)
    await session.commit()
    return {"results": results}


@router.get("/brief")
async def competitive_brief(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get a competitive intelligence brief."""
    return await get_competitive_brief(business, session)
