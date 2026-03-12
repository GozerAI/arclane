"""Billing — Stripe webhooks via Vinzy provisioning pipeline.

Flow:
  1. User pays via Stripe checkout
  2. Stripe webhook → Vinzy → creates license + Zuultimate tenant
  3. Vinzy calls Arclane webhook to provision the business
  4. Or: Arclane polls Vinzy to check license status

This module handles both directions:
  - POST /api/billing/webhook — Vinzy notifies Arclane of new subscriptions
  - GET /api/billing/status — Check billing status for a business
  - POST /api/billing/checkout — Generate a Stripe checkout URL (via Vinzy)
"""

import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from arclane.api.app import limiter
from arclane.api.deps import get_business
from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import Business

log = get_logger("billing")
router = APIRouter()


# --- Plans ---

PLANS = {
    "starter": {"price": 4900, "credits": 5, "name": "Starter"},
    "pro": {"price": 9900, "credits": 20, "name": "Pro"},
    "enterprise": {"price": 19900, "credits": 100, "name": "Enterprise"},
}


class CheckoutRequest(BaseModel):
    plan: str = "starter"


class CheckoutResponse(BaseModel):
    checkout_url: str


class WebhookPayload(BaseModel):
    event: str  # validated below
    license_key: str | None = None
    zuultimate_tenant_id: str | None = None
    customer_email: str
    plan: str
    business_slug: str | None = None

VALID_WEBHOOK_EVENTS = {
    "subscription.created", "subscription.cancelled", "subscription.renewed",
}


class BillingStatus(BaseModel):
    plan: str
    credits_remaining: int
    license_key: str | None
    active: bool


# --- Routes ---


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    business: Business = Depends(get_business),
):
    """Generate a Stripe checkout session via Vinzy."""
    if payload.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="Billing not enabled")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.vinzy_base_url}/api/v1/provisioning/checkout",
                json={
                    "product": "ARC",
                    "tier": payload.plan,
                    "customer_email": business.owner_email,
                    "metadata": {
                        "business_slug": business.slug,
                        "business_id": str(business.id),
                    },
                },
                headers={"X-Service-Token": settings.zuul_service_token},
            )
            resp.raise_for_status()
            data = resp.json()
        return CheckoutResponse(checkout_url=data["checkout_url"])
    except httpx.RequestError as e:
        log.error("Vinzy unreachable for checkout: %s", e)
        raise HTTPException(status_code=503, detail="Billing service unavailable")


@router.post("/webhook")
@limiter.limit("30/minute")
async def billing_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Handle billing events from Vinzy provisioning pipeline."""
    # Verify service token
    token = request.headers.get("X-Service-Token", "")
    if not token or token != settings.zuul_service_token:
        log.warning("Webhook rejected: invalid service token from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=403, detail="Forbidden")

    # Verify HMAC signature if signing secret is configured
    body = await request.body()
    if settings.webhook_signing_secret:
        signature = request.headers.get("X-Webhook-Signature", "")
        expected = hmac.new(
            settings.webhook_signing_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            log.warning("Webhook rejected: invalid HMAC signature")
            raise HTTPException(status_code=403, detail="Forbidden")

    payload = WebhookPayload.model_validate_json(body)

    if payload.event not in VALID_WEBHOOK_EVENTS:
        raise HTTPException(status_code=400, detail="Invalid event type")

    log.info("Billing webhook: %s for %s", payload.event, payload.customer_email)

    if payload.event == "subscription.created":
        # Find or create business
        business = None
        if payload.business_slug:
            result = await session.execute(
                select(Business).where(Business.slug == payload.business_slug)
            )
            business = result.scalar_one_or_none()

        if not business:
            result = await session.execute(
                select(Business).where(Business.owner_email == payload.customer_email)
            )
            business = result.scalar_one_or_none()

        if not business:
            log.warning("No business found for webhook: %s", payload.customer_email)
            return {"status": "skipped", "reason": "business not found"}

        plan_info = PLANS.get(payload.plan, PLANS["starter"])
        business.plan = payload.plan
        business.credits_remaining = plan_info["credits"]
        business.vinzy_license_key = payload.license_key
        business.zuultimate_tenant_id = payload.zuultimate_tenant_id
        await session.commit()

        log.info("Business %s upgraded to %s", business.slug, payload.plan)
        return {"status": "ok", "business": business.slug, "plan": payload.plan}

    elif payload.event == "subscription.cancelled":
        if payload.business_slug:
            result = await session.execute(
                select(Business).where(Business.slug == payload.business_slug)
            )
            business = result.scalar_one_or_none()
            if business:
                business.plan = "cancelled"
                business.credits_remaining = 0
                await session.commit()
                log.info("Business %s cancelled", business.slug)
                return {"status": "ok", "business": business.slug}

    elif payload.event == "subscription.renewed":
        if payload.business_slug:
            result = await session.execute(
                select(Business).where(Business.slug == payload.business_slug)
            )
            business = result.scalar_one_or_none()
            if business:
                plan_info = PLANS.get(payload.plan, PLANS["starter"])
                business.credits_remaining = plan_info["credits"]
                await session.commit()
                log.info("Business %s renewed — %d credits", business.slug, plan_info["credits"])
                return {"status": "ok", "business": business.slug}

    return {"status": "ok"}


@router.get("/status", response_model=BillingStatus)
async def get_billing_status(
    business: Business = Depends(get_business),
):
    """Get billing status for a business."""
    return BillingStatus(
        plan=business.plan,
        credits_remaining=business.credits_remaining + business.credits_bonus,
        license_key=business.vinzy_license_key,
        active=business.plan not in ("cancelled",),
    )
