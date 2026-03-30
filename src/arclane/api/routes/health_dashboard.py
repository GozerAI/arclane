"""Health dashboard API — scores, trends, recommendations."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business
from arclane.services.health_score_service import (
    calculate_health_score,
    get_health_trend,
    record_health_snapshot,
)

router = APIRouter()


@router.get("")
async def get_health_score(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get the current health score with sub-scores."""
    return await calculate_health_score(business, session)


@router.get("/trend")
async def get_trend(
    days: int = 30,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get health score trend over time."""
    return {"trend": await get_health_trend(business, session, days=days)}


@router.post("/snapshot")
async def record_snapshot(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Record a health score snapshot."""
    score = await record_health_snapshot(business, session)
    await session.commit()
    return {"score": score, "status": "recorded"}


@router.get("/recommendations")
async def get_recommendations(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get health-based recommendations."""
    result = await calculate_health_score(business, session)
    recs = []
    for area, score in result["sub_scores"].items():
        if score < 40:
            recs.append({"area": area, "score": score, "urgency": "high", "suggestion": f"Focus on improving {area.replace('_', ' ')}"})
        elif score < 60:
            recs.append({"area": area, "score": score, "urgency": "medium", "suggestion": f"Consider strengthening {area.replace('_', ' ')}"})
    return {"recommendations": sorted(recs, key=lambda x: x["score"]), "overall": result["overall"]}
