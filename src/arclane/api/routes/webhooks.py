"""Inbound webhooks — receive data from external tools (Zapier, n8n, analytics, CRM)."""

import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import Activity, Business, Content, Metric

log = get_logger("webhooks")

router = APIRouter()


def _verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    """Verify HMAC-SHA256 webhook signature."""
    if not signature or not secret:
        return True  # Skip verification if no secret configured
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# --- Content Performance Ingest ---

class ContentPerformancePayload(BaseModel):
    content_id: int
    metrics: list[dict] = Field(..., min_length=1)  # [{"name": "views", "value": 150}, ...]
    source: str = "webhook"


@router.post("/content-performance")
async def ingest_content_performance(
    payload: ContentPerformancePayload,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Receive content performance data from external analytics tools.

    Example: Zapier sends Google Analytics pageview data for a blog post.
    """
    content = await session.get(Content, payload.content_id)
    if not content or content.business_id != business.id:
        raise HTTPException(status_code=404, detail="Content not found")

    from arclane.services.content_analytics import record_performance

    recorded = []
    for metric in payload.metrics:
        name = metric.get("name", "")
        value = metric.get("value", 0)
        if name and isinstance(value, (int, float)):
            await record_performance(
                content.id, session,
                metric_name=name,
                value=float(value),
                source=payload.source,
            )
            recorded.append({"name": name, "value": value})

    await session.commit()
    log.info("Webhook: %d performance metrics ingested for content %d", len(recorded), payload.content_id)
    return {"status": "ok", "recorded": len(recorded), "metrics": recorded}


# --- External Leads Ingest ---

class LeadPayload(BaseModel):
    source: str = Field(..., max_length=100)  # "google_ads", "linkedin", "referral"
    email: str | None = Field(None, max_length=255)
    name: str | None = Field(None, max_length=255)
    metadata: dict | None = None


@router.post("/leads")
async def ingest_lead(
    payload: LeadPayload,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Receive lead notifications from CRM, forms, or ad platforms.

    Example: n8n fires when a new Typeform submission arrives.
    """
    session.add(Metric(
        business_id=business.id,
        name="lead_captured",
        value=1.0,
        metadata_json={
            "source": payload.source,
            "email": payload.email,
            "name": payload.name,
            **(payload.metadata or {}),
        },
    ))
    session.add(Activity(
        business_id=business.id,
        agent="system",
        action="Lead captured",
        detail=f"New lead from {payload.source}" + (f": {payload.name}" if payload.name else ""),
    ))
    await session.commit()
    log.info("Webhook: lead captured for %s from %s", business.slug, payload.source)
    return {"status": "ok", "source": payload.source}


# --- Distribution Feedback ---

class DistributionFeedbackPayload(BaseModel):
    content_id: int
    platform: str = Field(..., max_length=100)
    metrics: dict = Field(default_factory=dict)  # {"impressions": 500, "clicks": 25, "shares": 3}


@router.post("/distribution-feedback")
async def ingest_distribution_feedback(
    payload: DistributionFeedbackPayload,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Receive engagement data from social media tools (Buffer, Typefully, etc.)."""
    content = await session.get(Content, payload.content_id)
    if not content or content.business_id != business.id:
        raise HTTPException(status_code=404, detail="Content not found")

    from arclane.services.content_analytics import record_performance

    recorded = 0
    for metric_name, value in payload.metrics.items():
        if isinstance(value, (int, float)):
            await record_performance(
                content.id, session,
                metric_name=metric_name,
                value=float(value),
                source=f"distribution:{payload.platform}",
            )
            recorded += 1

    await session.commit()
    return {"status": "ok", "platform": payload.platform, "metrics_recorded": recorded}


# --- Custom Metric Ingest ---

class CustomMetricPayload(BaseModel):
    name: str = Field(..., max_length=100)
    value: float
    metadata: dict | None = None


@router.post("/metrics")
async def ingest_custom_metric(
    payload: CustomMetricPayload,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Push any custom metric from external tools.

    Example: Track signups, downloads, NPS scores, etc.
    """
    session.add(Metric(
        business_id=business.id,
        name=payload.name,
        value=payload.value,
        metadata_json=payload.metadata,
    ))
    await session.commit()
    return {"status": "ok", "metric": payload.name, "value": payload.value}


# --- Revenue Attribution Webhook ---

class RevenueAttributionPayload(BaseModel):
    amount_cents: int = Field(..., gt=0)
    source: str = Field(..., max_length=100)
    currency: str = Field("usd", max_length=10)
    content_id: int | None = None  # Which content piece drove this revenue
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    customer_email: str | None = None
    metadata: dict | None = None


@router.post("/revenue")
async def ingest_revenue_with_attribution(
    payload: RevenueAttributionPayload,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Receive revenue events with content/campaign attribution.

    Example: Stripe webhook -> Zapier -> Arclane with UTM + content_id attribution.
    """
    from arclane.services.revenue_tracker import record_revenue_event

    attribution = {
        "utm_source": payload.utm_source,
        "utm_medium": payload.utm_medium,
        "utm_campaign": payload.utm_campaign,
        "content_id": payload.content_id,
        "customer_email": payload.customer_email,
        **(payload.metadata or {}),
    }
    # Clean out None values
    attribution = {k: v for k, v in attribution.items() if v is not None}

    event = await record_revenue_event(
        business, session,
        source=payload.source,
        amount_cents=payload.amount_cents,
        currency=payload.currency,
        attribution=attribution,
    )
    await session.commit()

    log.info(
        "Webhook: revenue %d cents from %s for %s (content_id=%s)",
        payload.amount_cents, payload.source, business.slug, payload.content_id,
    )
    return {"status": "ok", "event_id": event.id, "amount_cents": payload.amount_cents}
