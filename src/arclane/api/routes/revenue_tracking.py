"""Revenue tracking API — events, summary, ROI, attribution, webhooks."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business
from arclane.services.revenue_tracker import (
    calculate_roi,
    get_attribution_summary,
    get_revenue_summary,
    record_revenue_event,
)

router = APIRouter()


class RevenueEventCreate(BaseModel):
    source: str = Field(..., max_length=100)
    amount_cents: int = Field(..., gt=0)
    currency: str = Field("usd", max_length=10)
    attribution: dict | None = None
    event_date: datetime | None = None


@router.post("/events")
async def create_revenue_event(
    payload: RevenueEventCreate,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Record a revenue event."""
    event = await record_revenue_event(
        business, session,
        source=payload.source,
        amount_cents=payload.amount_cents,
        currency=payload.currency,
        attribution=payload.attribution,
        event_date=payload.event_date,
    )
    await session.commit()
    return {"id": event.id, "amount_cents": event.amount_cents, "source": event.source}


@router.get("/summary")
async def revenue_summary(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get revenue summary with breakdowns."""
    return await get_revenue_summary(business, session)


@router.get("/roi")
async def revenue_roi(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Calculate ROI vs subscription cost."""
    return await calculate_roi(business, session)


@router.get("/attribution")
async def revenue_attribution(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get revenue attribution breakdown."""
    return {"attribution": await get_attribution_summary(business, session)}


class WebhookPayload(BaseModel):
    source: str
    event_type: str
    amount_cents: int = 0
    currency: str = "usd"
    metadata: dict | None = None


@router.post("/webhook")
async def revenue_webhook(
    payload: WebhookPayload,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Receive revenue webhooks from external payment providers."""
    if payload.event_type in ("charge.succeeded", "payment.completed", "order.completed"):
        event = await record_revenue_event(
            business, session,
            source=payload.source,
            amount_cents=payload.amount_cents,
            currency=payload.currency,
            attribution=payload.metadata,
        )
        await session.commit()
        return {"status": "recorded", "event_id": event.id}
    return {"status": "ignored", "event_type": payload.event_type}
