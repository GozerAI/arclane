"""Billing routes backed by Stripe checkout sessions through Vinzy."""

import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.app import limiter
from arclane.api.deps import get_business
from arclane.billing.policy import (
    AD_SPEND_TAKE_PERCENT,
    ADD_ON_POLICIES,
    CREDIT_PACK_POLICIES,
    PLAN_POLICIES,
    PUBLIC_PLANS,
    REVENUE_SHARE_PERCENT,
    STRIPE_FEE_FIXED_CENTS,
    STRIPE_FEE_PERCENT,
    company_limit_for_account,
    effective_credit_value_cents,
    get_plan_policy,
)
from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.engine.operating_plan import enqueue_add_on
from arclane.models.tables import Activity, Business

log = get_logger("billing")
router = APIRouter()
provision_router = APIRouter()


PLANS = {
    key: {
        "price": plan.price_cents,
        "credits": plan.credits,
        "name": plan.name,
        "company_limit": plan.company_limit,
        "trial_days": plan.trial_days,
        "checkout_enabled": plan.checkout_enabled,
    }
    for key, plan in PUBLIC_PLANS.items()
}


class CreditPackStatus(BaseModel):
    key: str
    name: str
    credits: int
    price_cents: int


class AddOnStatus(BaseModel):
    key: str
    name: str
    included_cycles: int
    price_cents: int


class CheckoutRequest(BaseModel):
    plan: str | None = None
    credit_pack: str | None = None
    add_on: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str


class WebhookPayload(BaseModel):
    event: str
    customer_email: str
    plan: str = "preview"
    license_key: str | None = None
    zuultimate_tenant_id: str | None = None
    business_slug: str | None = None
    credit_pack: str | None = None
    credits_purchased: int | None = None
    add_on: str | None = None


VALID_WEBHOOK_EVENTS = {
    "subscription.created",
    "subscription.cancelled",
    "subscription.renewed",
    "credits.purchased",
    "add_on.purchased",
}


class BillingStatus(BaseModel):
    plan: str
    credits_remaining: int
    license_key: str | None
    active: bool
    credits_included: int
    company_limit: int
    company_count: int
    company_slots_remaining: int
    effective_credit_value_cents: int | None
    trial_days: int | None
    revenue_share_percent: float
    ad_spend_take_percent: float
    stripe_fee_percent: float
    stripe_fee_fixed_cents: int
    can_start_paid_trial: bool
    subscription_required_for_speed: bool
    credit_packs: list[CreditPackStatus]
    add_ons: list[AddOnStatus]


def _build_checkout_metadata(business: Business) -> dict[str, str]:
    return {
        "business_slug": business.slug,
        "business_id": str(business.id),
        "customer_email": business.owner_email,
    }


async def _apply_add_on_purchase(
    business: Business,
    add_on_key: str,
    session: AsyncSession,
) -> dict:
    policy = ADD_ON_POLICIES.get(add_on_key)
    if not policy:
        raise HTTPException(status_code=400, detail="Invalid add-on")

    operating_plan = (business.agent_config or {}).get("operating_plan") or {}
    offers = operating_plan.get("add_on_offers") or []
    offer = next((item for item in offers if item.get("key") == add_on_key), None)
    if not offer:
        raise HTTPException(status_code=400, detail="Add-on unavailable for this business")
    if offer.get("status") == "purchased":
        return {"status": "ok", "business": business.slug, "add_on": add_on_key, "already_applied": True}

    updated_agent_config = dict(business.agent_config or {})
    updated_agent_config["operating_plan"] = enqueue_add_on(operating_plan, add_on_key)
    business.agent_config = updated_agent_config
    session.add(
        Activity(
            business_id=business.id,
            agent="system",
            action="Add-on purchased",
            detail=(
                f"{policy.name} was purchased through Stripe and moved ahead of the normal queue "
                f"with {policy.included_cycles} included night"
                f"{'' if policy.included_cycles == 1 else 's'}."
            ),
        )
    )
    await session.commit()
    log.info("Business %s purchased add-on %s", business.slug, add_on_key)
    return {"status": "ok", "business": business.slug, "add_on": add_on_key}


async def _find_business_for_payload(
    payload: WebhookPayload,
    session: AsyncSession,
) -> Business | None:
    business = None
    if payload.business_slug:
        result = await session.execute(
            select(Business).where(Business.slug == payload.business_slug)
        )
        business = result.scalar_one_or_none()

    if business:
        return business

    result = await session.execute(
        select(Business).where(Business.owner_email == payload.customer_email)
    )
    return result.scalar_one_or_none()


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    business: Business = Depends(get_business),
):
    """Generate a Stripe checkout session via Vinzy."""
    wants_plan = bool(payload.plan)
    wants_credit_pack = bool(payload.credit_pack)
    wants_add_on = bool(payload.add_on)
    selected_targets = sum([wants_plan, wants_credit_pack, wants_add_on])
    if selected_targets != 1:
        raise HTTPException(status_code=400, detail="Provide exactly one purchase target")

    metadata = _build_checkout_metadata(business)

    if wants_plan:
        if payload.plan not in PLANS:
            raise HTTPException(status_code=400, detail="Invalid plan")
        if not PLANS[payload.plan]["checkout_enabled"]:
            raise HTTPException(status_code=400, detail="This tier is not available for paid checkout")

        product = "ARC"
        tier = payload.plan
        metadata.update({
            "trial_days": str(PLANS[payload.plan]["trial_days"] or 0),
            "card_required": "true",
            "checkout_kind": "subscription",
            "billing_mode": "subscription",
            "subscription_managed_by": "stripe",
        })
        billing_cycle = "monthly"
    elif wants_credit_pack:
        pack = CREDIT_PACK_POLICIES.get(payload.credit_pack or "")
        if not pack:
            raise HTTPException(status_code=400, detail="Invalid credit pack")

        product = "ARC-CREDITS"
        tier = pack.key
        metadata.update({
            "credit_pack": pack.key,
            "credits": str(pack.credits),
            "card_required": "true",
            "checkout_kind": "credit_pack",
            "billing_mode": "payment",
        })
        billing_cycle = "one_time"
    else:
        add_on = ADD_ON_POLICIES.get(payload.add_on or "")
        if not add_on:
            raise HTTPException(status_code=400, detail="Invalid add-on")
        operating_plan = (business.agent_config or {}).get("operating_plan") or {}
        offer = next(
            (item for item in (operating_plan.get("add_on_offers") or []) if item.get("key") == add_on.key),
            None,
        )
        if not offer or offer.get("status") != "available":
            raise HTTPException(status_code=400, detail="This add-on is not available yet")

        product = "ARC-ADDON"
        tier = add_on.key
        metadata.update({
            "add_on": add_on.key,
            "included_cycles": str(add_on.included_cycles),
            "checkout_kind": "add_on",
            "billing_mode": "payment",
        })
        billing_cycle = "one_time"

    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="Billing not enabled")

    # Build callback URLs for Stripe checkout
    base = f"https://{settings.domain}" if settings.env == "production" else "http://localhost:8012"
    success_url = f"{base}/dashboard?checkout=success"
    cancel_url = f"{base}/dashboard?checkout=cancelled"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.vinzy_base_url}/api/v1/checkout/create",
                json={
                    "product_code": product,
                    "tier": tier,
                    "billing_cycle": billing_cycle,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "metadata": metadata,
                },
                headers={"X-Service-Token": settings.zuul_service_token},
            )
            resp.raise_for_status()
            data = resp.json()
        return CheckoutResponse(checkout_url=data["url"])
    except httpx.RequestError as exc:
        log.error("Vinzy unreachable for checkout: %s", exc)
        raise HTTPException(status_code=503, detail="Billing service unavailable")


@router.post("/webhook")
@limiter.limit("30/minute")
async def billing_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Handle billing events from Vinzy provisioning pipeline."""
    token = request.headers.get("X-Service-Token", "")
    if not token or token != settings.zuul_service_token:
        log.warning(
            "Webhook rejected: invalid service token from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=403, detail="Forbidden")

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
    business = await _find_business_for_payload(payload, session)
    if not business:
        log.warning("No business found for webhook: %s", payload.customer_email)
        return {"status": "skipped", "reason": "business not found"}

    if payload.event == "subscription.created":
        plan_info = PLANS.get(payload.plan, PLANS["pro"])
        business.plan = payload.plan
        business.credits_remaining = plan_info["credits"]
        business.vinzy_license_key = payload.license_key
        business.zuultimate_tenant_id = payload.zuultimate_tenant_id
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription active",
                detail=(
                    f"{plan_info['name']} is live. Stripe manages recurring billing and "
                    f"credits were reset to {plan_info['credits']}."
                ),
            )
        )
        await session.commit()
        log.info("Business %s upgraded to %s", business.slug, payload.plan)
        return {"status": "ok", "business": business.slug, "plan": payload.plan}

    if payload.event == "subscription.cancelled":
        business.plan = "cancelled"
        business.credits_remaining = 0
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription cancelled",
                detail="Recurring billing ended and included credits were cleared.",
            )
        )
        await session.commit()
        log.info("Business %s cancelled", business.slug)
        return {"status": "ok", "business": business.slug}

    if payload.event == "subscription.renewed":
        plan_info = PLANS.get(payload.plan, PLANS["pro"])
        business.credits_remaining = plan_info["credits"]
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription renewed",
                detail=f"Monthly credits refreshed to {plan_info['credits']}.",
            )
        )
        await session.commit()
        log.info("Business %s renewed - %d credits", business.slug, plan_info["credits"])
        return {"status": "ok", "business": business.slug}

    if payload.event == "add_on.purchased":
        if not payload.add_on:
            raise HTTPException(status_code=400, detail="Add-on purchase webhook missing add-on key")
        return await _apply_add_on_purchase(business, payload.add_on, session)

    pack = CREDIT_PACK_POLICIES.get(payload.credit_pack or "")
    purchased_credits = payload.credits_purchased or (pack.credits if pack else 0)
    if purchased_credits <= 0:
        raise HTTPException(status_code=400, detail="Credit purchase webhook missing credits")

    business.credits_bonus += purchased_credits
    session.add(
        Activity(
            business_id=business.id,
            agent="system",
            action="Credits added",
            detail=f"{purchased_credits} additional credits are now available for burst usage.",
        )
    )
    await session.commit()
    log.info("Business %s received %d purchased credits", business.slug, purchased_credits)
    return {"status": "ok", "business": business.slug, "credits_added": purchased_credits}


class ProvisioningCompletePayload(BaseModel):
    """Generic provisioning.complete event from Vinzy."""
    event: str
    product_code: str
    tier: str
    customer_email: str
    customer_name: str = ""
    company: str = ""
    license_key: str | None = None
    zuultimate_tenant_id: str | None = None
    billing_cycle: str = "monthly"
    metadata: dict = {}


@provision_router.post("/provision-complete")
@limiter.limit("30/minute")
async def provision_complete(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Handle provisioning.complete callback from Vinzy.

    This is the top-level webhook that Vinzy POSTs to after a successful
    Stripe checkout → license creation → Zuultimate tenant provisioning.
    """
    token = request.headers.get("X-Service-Token", "")
    if not token or token != settings.zuul_service_token:
        log.warning("Provision callback rejected: invalid service token")
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.body()
    payload = ProvisioningCompletePayload.model_validate_json(body)

    if payload.event != "provisioning.complete":
        raise HTTPException(status_code=400, detail="Unexpected event type")

    # Find business by slug (from checkout metadata) or by email
    business = None
    slug = payload.metadata.get("business_slug")
    if slug:
        result = await session.execute(
            select(Business).where(Business.slug == slug)
        )
        business = result.scalar_one_or_none()

    if not business:
        result = await session.execute(
            select(Business).where(Business.owner_email == payload.customer_email)
        )
        business = result.scalar_one_or_none()

    if not business:
        log.warning("No business found for provisioning callback: %s", payload.customer_email)
        return {"status": "skipped", "reason": "business not found"}

    checkout_kind = payload.metadata.get("checkout_kind")
    is_credit_pack = checkout_kind == "credit_pack"
    is_add_on = checkout_kind == "add_on"

    if is_credit_pack:
        credits_str = payload.metadata.get("credits", "0")
        purchased = int(credits_str) if credits_str.isdigit() else 0
        if purchased <= 0:
            raise HTTPException(status_code=400, detail="Credit purchase missing credits count")
        business.credits_bonus += purchased
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Credits added",
                detail=f"{purchased} additional credits are now available.",
            )
        )
        await session.commit()
        log.info("Business %s received %d credits via provisioning", business.slug, purchased)
        return {"status": "ok", "business": business.slug, "credits_added": purchased}

    if is_add_on:
        add_on_key = payload.metadata.get("add_on", "")
        if not add_on_key:
            raise HTTPException(status_code=400, detail="Add-on purchase missing add-on key")
        return await _apply_add_on_purchase(business, add_on_key, session)

    # Subscription provisioned
    plan_info = PLANS.get(payload.tier, PLANS.get("pro"))
    business.plan = payload.tier
    business.credits_remaining = plan_info["credits"] if plan_info else 0
    business.vinzy_license_key = payload.license_key
    business.zuultimate_tenant_id = payload.zuultimate_tenant_id
    session.add(
        Activity(
            business_id=business.id,
            agent="system",
            action="Subscription active",
            detail=f"{plan_info['name'] if plan_info else payload.tier} plan provisioned via Stripe.",
        )
    )
    await session.commit()
    log.info("Business %s provisioned: plan=%s", business.slug, payload.tier)
    return {"status": "ok", "business": business.slug, "plan": payload.tier}


@router.get("/status", response_model=BillingStatus)
async def get_billing_status(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get billing status for a business."""
    result = await session.execute(
        select(Business.plan)
        .where(Business.owner_email == business.owner_email)
        .where(~Business.slug.startswith("_user-"))
    )
    owned_plans = [row[0] for row in result.all()]
    policy = get_plan_policy(business.plan)
    company_limit = company_limit_for_account(owned_plans)
    company_count = len(owned_plans)
    trial_days = policy.trial_days
    if business.plan == "preview":
        trial_days = PLAN_POLICIES["starter"].trial_days

    return BillingStatus(
        plan=business.plan,
        credits_remaining=business.credits_remaining + business.credits_bonus,
        license_key=business.vinzy_license_key,
        active=business.plan not in ("cancelled",),
        credits_included=policy.credits,
        company_limit=company_limit,
        company_count=company_count,
        company_slots_remaining=max(company_limit - company_count, 0),
        effective_credit_value_cents=effective_credit_value_cents(business.plan),
        trial_days=trial_days,
        revenue_share_percent=REVENUE_SHARE_PERCENT,
        ad_spend_take_percent=AD_SPEND_TAKE_PERCENT,
        stripe_fee_percent=STRIPE_FEE_PERCENT,
        stripe_fee_fixed_cents=STRIPE_FEE_FIXED_CENTS,
        can_start_paid_trial=business.plan == "preview",
        subscription_required_for_speed=business.plan == "preview",
        credit_packs=[
            CreditPackStatus(
                key=pack.key,
                name=pack.name,
                credits=pack.credits,
                price_cents=pack.price_cents,
            )
            for pack in CREDIT_PACK_POLICIES.values()
        ],
        add_ons=[
            AddOnStatus(
                key=policy.key,
                name=policy.name,
                included_cycles=policy.included_cycles,
                price_cents=policy.price_cents,
            )
            for policy in ADD_ON_POLICIES.values()
        ],
    )
