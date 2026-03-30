"""Forecast API — roadmap velocity, graduation ETA, bottlenecks, weekly focus."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business
from arclane.services.roadmap_forecaster import compute_forecast

router = APIRouter()


@router.get("")
async def get_forecast(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get the full roadmap forecast: velocity, ETA, pace, bottlenecks, focus."""
    return await compute_forecast(business, session)


@router.get("/pace")
async def get_pace(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get just the pace assessment (ahead/on-track/behind)."""
    forecast = await compute_forecast(business, session)
    return {
        "pace": forecast["pace"],
        "velocity": forecast["velocity"],
    }


@router.get("/bottlenecks")
async def get_bottlenecks(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get detected bottlenecks and recommendations."""
    forecast = await compute_forecast(business, session)
    return {
        "bottlenecks": forecast["bottlenecks"],
        "weekly_focus": forecast["weekly_focus"],
    }
