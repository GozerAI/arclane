"""Benchmarks API — cohort comparison and percentile scoring."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business
from arclane.services.benchmarks import compute_benchmarks

router = APIRouter()


@router.get("")
async def get_benchmarks(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get cohort benchmark comparison for this business."""
    return await compute_benchmarks(business, session)
