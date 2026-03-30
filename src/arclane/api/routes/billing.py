"""Billing routes backed by Stripe checkout sessions through Vinzy."""

import hashlib
import hmac
import traceback
from datetime import timedelta

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
    DAY_PACK_POLICIES,
    PLAN_POLICIES,
    PUBLIC_PLANS,
    REVENUE_SHARE_PERCENT,
    STRIPE_FEE_FIXED_CENTS,
    STRIPE_FEE_PERCENT,
    company_limit_for_account,
    effective_day_value_cents,
    get_plan_policy,
)
from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.engine.operating_plan import enqueue_add_on
from arclane.models.tables import Activity, Business, FailedWebhook

log = get_logger("billing")
router = APIRouter()
provision_router = APIRouter()
stripe_direct_router = APIRouter()


PLANS = {
    key: {
        "price": plan.price_cents,
        "working_days": plan.working_days,
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
    working_days: int
    price_cents: int


class AddOnStatus(BaseModel):
    key: str
    name: str
    included_cycles: int
    price_cents: int


class CheckoutRequest(BaseModel):
    plan: str | None = None
    day_pack: str | None = None
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
    day_pack: str | None = None
    working_days_purchased: int | None = None
    add_on: str | None = None
    webhook_event_id: str | None = None


VALID_WEBHOOK_EVENTS = {
    "subscription.created",
    "subscription.cancelled",
    "subscription.renewed",
    "credits.purchased",
    "add_on.purchased",
}


class BillingStatus(BaseModel):
    plan: str
    working_days_remaining: int
    license_key: str | None
    active: bool
    working_days_included: int
    company_limit: int
    company_count: int
    company_slots_remaining: int
    effective_day_value_cents: int | None
    trial_days: int | None
    revenue_share_percent: float
    ad_spend_take_percent: float
    stripe_fee_percent: float
    stripe_fee_fixed_cents: int
    can_start_paid_trial: bool
    subscription_required_for_speed: bool
    day_packs: list[CreditPackStatus]
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
    updated_agent_config["operating_plan"] = enqueue_add_on(operating_plan, add_on_key, phase=getattr(business, "current_phase", 0) or 0, day=getattr(business, "roadmap_day", 0) or 0)
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
    wants_day_pack = bool(payload.day_pack)
    wants_add_on = bool(payload.add_on)
    selected_targets = sum([wants_plan, wants_day_pack, wants_add_on])
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
    elif wants_day_pack:
        pack = DAY_PACK_POLICIES.get(payload.day_pack or "")
        if not pack:
            raise HTTPException(status_code=400, detail="Invalid day pack")

        product = "ARC-CREDITS"
        tier = pack.key
        metadata.update({
            "day_pack": pack.key,
            "credits": str(pack.working_days),
            "card_required": "true",
            "checkout_kind": "day_pack",
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
                headers={"X-Service-Token": settings.zuul_service_token, "X-Service-Name": "arclane"},
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

    # Idempotency: skip already-processed webhooks
    if payload.webhook_event_id:
        existing = await session.execute(
            select(Activity.id).where(
                Activity.metadata_json["webhook_event_id"].as_string() == payload.webhook_event_id,
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            log.info("Duplicate webhook skipped: %s", payload.webhook_event_id)
            return {"status": "ok", "duplicate": True}

    log.info("Billing webhook: %s for %s", payload.event, payload.customer_email)

    try:
        return await _process_webhook_payload(payload, session)
    except HTTPException:
        raise  # re-raise validation errors (400s) as-is
    except Exception as exc:
        log.exception("Webhook processing failed for %s — queued for retry", payload.event)
        from arclane.models.tables import _utcnow
        session.add(FailedWebhook(
            endpoint="billing_webhook",
            payload=payload.model_dump(),
            error=traceback.format_exc(),
            next_retry_at=_utcnow() + timedelta(minutes=2),
        ))
        await session.commit()
        return {"status": "queued_for_retry", "event": payload.event}


async def _process_webhook_payload(
    payload: WebhookPayload,
    session: AsyncSession,
) -> dict:
    """Core webhook processing logic — separated for retry support."""
    business = await _find_business_for_payload(payload, session)
    if not business:
        log.warning("No business found for webhook: %s", payload.customer_email)
        return {"status": "skipped", "reason": "business not found"}

    # Attach webhook_event_id to every Activity for idempotency tracking
    wh_meta = {"webhook_event_id": payload.webhook_event_id} if payload.webhook_event_id else None

    if payload.event == "subscription.created":
        plan_info = PLANS.get(payload.plan, PLANS["pro"])
        business.plan = payload.plan
        business.working_days_remaining = plan_info["working_days"]
        business.vinzy_license_key = payload.license_key
        business.zuultimate_tenant_id = payload.zuultimate_tenant_id
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription active",
                detail=(
                    f"{plan_info['name']} is live. Stripe manages recurring billing and "
                    f"working days were reset to {plan_info['working_days']}."
                ),
                metadata_json=wh_meta,
            )
        )
        await session.commit()
        log.info("Business %s upgraded to %s", business.slug, payload.plan)
        return {"status": "ok", "business": business.slug, "plan": payload.plan}

    if payload.event == "subscription.cancelled":
        business.plan = "cancelled"
        business.working_days_remaining = 0
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription cancelled",
                detail="Recurring billing ended and included working days were cleared.",
                metadata_json=wh_meta,
            )
        )
        await session.commit()
        log.info("Business %s cancelled", business.slug)
        return {"status": "ok", "business": business.slug}

    if payload.event == "subscription.renewed":
        plan_info = PLANS.get(payload.plan, PLANS["pro"])
        business.working_days_remaining = plan_info["working_days"]
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription renewed",
                detail=f"Monthly working days refreshed to {plan_info['working_days']}.",
                metadata_json=wh_meta,
            )
        )
        await session.commit()
        log.info("Business %s renewed - %d working days", business.slug, plan_info["working_days"])
        return {"status": "ok", "business": business.slug}

    if payload.event == "add_on.purchased":
        if not payload.add_on:
            raise HTTPException(status_code=400, detail="Add-on purchase webhook missing add-on key")
        return await _apply_add_on_purchase(business, payload.add_on, session)

    pack = DAY_PACK_POLICIES.get(payload.day_pack or "")
    purchased_working_days = payload.working_days_purchased or (pack.working_days if pack else 0)
    if purchased_working_days <= 0:
        raise HTTPException(status_code=400, detail="Working day purchase webhook missing count")

    business.working_days_bonus += purchased_working_days
    session.add(
        Activity(
            business_id=business.id,
            agent="system",
            action="Credits added",
            detail=f"{purchased_working_days} additional working days are now available for burst usage.",
            metadata_json=wh_meta,
        )
    )
    await session.commit()
    log.info("Business %s received %d purchased working days", business.slug, purchased_working_days)
    return {"status": "ok", "business": business.slug, "working_days_added": purchased_working_days}


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
    is_day_pack = checkout_kind == "day_pack"
    is_add_on = checkout_kind == "add_on"

    if is_day_pack:
        working_days_str = payload.metadata.get("credits", "0")
        purchased = int(working_days_str) if working_days_str.isdigit() else 0
        if purchased <= 0:
            raise HTTPException(status_code=400, detail="Working day purchase missing count")
        business.working_days_bonus += purchased
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Credits added",
                detail=f"{purchased} additional working days are now available.",
            )
        )
        await session.commit()
        log.info("Business %s received %d working days via provisioning", business.slug, purchased)
        return {"status": "ok", "business": business.slug, "working_days_added": purchased}

    if is_add_on:
        add_on_key = payload.metadata.get("add_on", "")
        if not add_on_key:
            raise HTTPException(status_code=400, detail="Add-on purchase missing add-on key")
        return await _apply_add_on_purchase(business, add_on_key, session)

    # Subscription provisioned
    plan_info = PLANS.get(payload.tier, PLANS.get("pro"))
    business.plan = payload.tier
    business.working_days_remaining = plan_info["working_days"] if plan_info else 0
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


# ─────────────────────────────────────────────────────────────
# Direct Stripe Webhook — Safety Net
# ─────────────────────────────────────────────────────────────
# This endpoint receives raw Stripe webhook events directly,
# acting as a safety net alongside the primary Vinzy pipeline.
#
# Flow with both active:
#   Stripe event → Vinzy webhook → processes → calls Arclane /webhook
#   Stripe event → Arclane /stripe-direct → checks idempotency → skips if already handled
#
# If Vinzy is down:
#   Stripe event → Arclane /stripe-direct → processes directly
#
# Setup:
#   1. Go to Stripe Dashboard > Developers > Webhooks > + Add destination
#   2. URL: https://arclane.cloud/api/businesses/{slug}/billing/stripe-direct
#      (or use the global mount — see below)
#   3. Events: checkout.session.completed, customer.subscription.updated,
#      customer.subscription.deleted, invoice.paid, invoice.payment_failed
#   4. Copy the signing secret into ARCLANE_STRIPE_DIRECT_WEBHOOK_SECRET
#
# Local testing:
#   stripe listen --forward-to http://localhost:8012/api/billing/stripe-direct


@stripe_direct_router.post("/stripe-direct")
@limiter.limit("60/minute")
async def stripe_direct_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Handle raw Stripe events as a safety net for the Vinzy pipeline."""
    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="Billing not enabled")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # --- Verify the Stripe webhook signature ---
    if settings.stripe_direct_webhook_secret:
        try:
            import stripe
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.stripe_direct_webhook_secret,
            )
        except Exception as e:
            log.warning("Stripe direct webhook signature failed: %s", e)
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        # No secret configured — parse without verification (dev only).
        import json as _json
        event = _json.loads(payload)
        log.warning("Stripe direct webhook: no signing secret — skipping verification")

    event_type = event.get("type", "") if isinstance(event, dict) else event.type
    event_id = event.get("id", "") if isinstance(event, dict) else event.id
    data_obj = (
        event.get("data", {}).get("object", {})
        if isinstance(event, dict)
        else event.data.object
    )

    log.info("Stripe direct webhook: %s (event %s)", event_type, event_id)

    # --- Idempotency: skip if this Stripe event was already processed ---
    if event_id:
        existing = await session.execute(
            select(Activity.id).where(
                Activity.metadata_json["stripe_event_id"].as_string() == event_id,
            ).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            log.info("Stripe direct webhook: duplicate event %s — skipped", event_id)
            return {"status": "ok", "duplicate": True}

    stripe_meta = {"stripe_event_id": event_id}

    # --- Route by event type ---

    if event_type == "checkout.session.completed":
        return await _handle_checkout_completed(data_obj, session, stripe_meta)

    if event_type == "customer.subscription.updated":
        return await _handle_subscription_updated(data_obj, session, stripe_meta)

    if event_type == "customer.subscription.deleted":
        return await _handle_subscription_deleted(data_obj, session, stripe_meta)

    if event_type == "invoice.paid":
        return await _handle_invoice_paid(data_obj, session, stripe_meta)

    if event_type == "invoice.payment_failed":
        return await _handle_invoice_failed(data_obj, session, stripe_meta)

    log.info("Stripe direct webhook: unhandled event type %s", event_type)
    return {"status": "ok", "unhandled": True}


async def _find_business_by_metadata_or_email(
    metadata: dict,
    email: str | None,
    session: AsyncSession,
) -> Business | None:
    """Look up a business from checkout metadata or customer email."""
    slug = metadata.get("business_slug")
    if slug:
        result = await session.execute(
            select(Business).where(Business.slug == slug)
        )
        biz = result.scalar_one_or_none()
        if biz:
            return biz

    cust_email = metadata.get("customer_email") or email
    if cust_email:
        result = await session.execute(
            select(Business).where(Business.owner_email == cust_email)
        )
        return result.scalar_one_or_none()

    return None


async def _handle_checkout_completed(
    data: dict,
    session: AsyncSession,
    stripe_meta: dict,
) -> dict:
    """Process a completed checkout — subscription, day pack, or add-on."""
    metadata = data.get("metadata", {})
    checkout_kind = metadata.get("checkout_kind", "")
    customer_email = data.get("customer_details", {}).get("email", "")

    business = await _find_business_by_metadata_or_email(
        metadata, customer_email, session
    )
    if not business:
        log.warning(
            "Stripe checkout.session.completed: no business found "
            "(slug=%s, email=%s)", metadata.get("business_slug"), customer_email
        )
        return {"status": "skipped", "reason": "business not found"}

    if checkout_kind == "subscription":
        plan_key = metadata.get("checkout_kind_tier") or data.get("metadata", {}).get("tier", "pro")
        # Try to extract the plan from the product/price. Fall back to metadata.
        # The Vinzy checkout stores the plan as the tier in metadata.
        for key in ("tier", "plan"):
            if key in metadata:
                plan_key = metadata[key]
                break

        plan_info = PLANS.get(plan_key, PLANS.get("pro"))
        if not plan_info:
            plan_info = PLANS["pro"]
        business.plan = plan_key
        business.working_days_remaining = plan_info["working_days"]
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription active",
                detail=(
                    f"{plan_info['name']} activated via Stripe checkout "
                    f"(direct webhook safety net)."
                ),
                metadata_json=stripe_meta,
            )
        )
        await session.commit()
        log.info(
            "Stripe direct: business %s activated plan %s",
            business.slug, plan_key,
        )
        return {"status": "ok", "business": business.slug, "plan": plan_key}

    if checkout_kind == "day_pack":
        pack_key = metadata.get("day_pack", "")
        credits_str = metadata.get("credits", "0")
        purchased = int(credits_str) if credits_str.isdigit() else 0
        if purchased <= 0:
            pack = DAY_PACK_POLICIES.get(pack_key)
            purchased = pack.working_days if pack else 0
        if purchased <= 0:
            return {"status": "skipped", "reason": "invalid day pack"}

        business.working_days_bonus += purchased
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Credits added",
                detail=(
                    f"{purchased} working days added via Stripe checkout "
                    f"(direct webhook safety net)."
                ),
                metadata_json=stripe_meta,
            )
        )
        await session.commit()
        log.info(
            "Stripe direct: business %s credited %d working days",
            business.slug, purchased,
        )
        return {"status": "ok", "business": business.slug, "working_days_added": purchased}

    if checkout_kind == "add_on":
        add_on_key = metadata.get("add_on", "")
        if not add_on_key:
            return {"status": "skipped", "reason": "missing add-on key"}
        result = await _apply_add_on_purchase(business, add_on_key, session)
        # Tag with stripe_meta for idempotency on future runs.
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Add-on confirmed",
                detail=f"Add-on {add_on_key} confirmed via direct webhook.",
                metadata_json=stripe_meta,
            )
        )
        await session.commit()
        return result

    log.info("Stripe checkout completed with unknown kind: %s", checkout_kind)
    return {"status": "ok", "unknown_kind": checkout_kind}


async def _handle_subscription_updated(
    data: dict,
    session: AsyncSession,
    stripe_meta: dict,
) -> dict:
    """Handle subscription changes — upgrades, downgrades, cancellation scheduling."""
    customer_email = data.get("customer_email", "")
    metadata = data.get("metadata", {})

    business = await _find_business_by_metadata_or_email(
        metadata, customer_email, session
    )
    if not business:
        log.info("Stripe subscription.updated: no matching business")
        return {"status": "skipped", "reason": "business not found"}

    status = data.get("status", "")
    cancel_at_period_end = data.get("cancel_at_period_end", False)

    # Detect plan change from the subscription items.
    items = data.get("items", {}).get("data", [])
    new_price_id = items[0].get("price", {}).get("id", "") if items else ""

    if cancel_at_period_end:
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Cancellation scheduled",
                detail="Subscription will cancel at end of billing period.",
                metadata_json=stripe_meta,
            )
        )
        await session.commit()
        log.info("Stripe direct: %s cancellation scheduled", business.slug)
    elif status == "active" and not cancel_at_period_end:
        # Possible reactivation or plan change.
        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Subscription updated",
                detail=f"Subscription status: {status}, price: {new_price_id}.",
                metadata_json=stripe_meta,
            )
        )
        await session.commit()

    return {"status": "ok", "business": business.slug}


async def _handle_subscription_deleted(
    data: dict,
    session: AsyncSession,
    stripe_meta: dict,
) -> dict:
    """Handle subscription cancellation — revoke access."""
    customer_email = data.get("customer_email", "")
    metadata = data.get("metadata", {})

    business = await _find_business_by_metadata_or_email(
        metadata, customer_email, session
    )
    if not business:
        log.info("Stripe subscription.deleted: no matching business")
        return {"status": "skipped", "reason": "business not found"}

    business.plan = "cancelled"
    business.working_days_remaining = 0
    session.add(
        Activity(
            business_id=business.id,
            agent="system",
            action="Subscription cancelled",
            detail="Subscription fully cancelled (direct webhook safety net).",
            metadata_json=stripe_meta,
        )
    )
    await session.commit()
    log.info("Stripe direct: %s subscription cancelled", business.slug)
    return {"status": "ok", "business": business.slug}


async def _handle_invoice_paid(
    data: dict,
    session: AsyncSession,
    stripe_meta: dict,
) -> dict:
    """Handle successful invoice — refresh working days on renewal."""
    customer_email = data.get("customer_email", "")
    metadata = data.get("metadata", {})
    billing_reason = data.get("billing_reason", "")

    business = await _find_business_by_metadata_or_email(
        metadata, customer_email, session
    )
    if not business:
        return {"status": "skipped", "reason": "business not found"}

    # Only refresh working days on subscription renewals, not the
    # initial invoice (which is handled by checkout.session.completed).
    if billing_reason == "subscription_cycle":
        plan_info = PLANS.get(business.plan)
        if plan_info:
            business.working_days_remaining = plan_info["working_days"]
            session.add(
                Activity(
                    business_id=business.id,
                    agent="system",
                    action="Subscription renewed",
                    detail=(
                        f"Monthly working days refreshed to "
                        f"{plan_info['working_days']} (direct webhook safety net)."
                    ),
                    metadata_json=stripe_meta,
                )
            )
            await session.commit()
            log.info(
                "Stripe direct: %s renewed — %d working days",
                business.slug, plan_info["working_days"],
            )

    return {"status": "ok", "business": business.slug}


async def _handle_invoice_failed(
    data: dict,
    session: AsyncSession,
    stripe_meta: dict,
) -> dict:
    """Handle failed invoice — log for follow-up."""
    customer_email = data.get("customer_email", "")
    metadata = data.get("metadata", {})

    business = await _find_business_by_metadata_or_email(
        metadata, customer_email, session
    )
    if not business:
        return {"status": "skipped", "reason": "business not found"}

    session.add(
        Activity(
            business_id=business.id,
            agent="system",
            action="Payment failed",
            detail=(
                "Subscription invoice payment failed. "
                "Customer may need to update their payment method."
            ),
            metadata_json=stripe_meta,
        )
    )
    await session.commit()
    log.warning("Stripe direct: %s payment failed", business.slug)
    # TODO: Send notification email prompting the user to update payment method.
    return {"status": "ok", "business": business.slug, "action_needed": True}


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
        working_days_remaining=business.working_days_remaining + business.working_days_bonus,
        license_key=business.vinzy_license_key,
        active=business.plan not in ("cancelled",),
        working_days_included=policy.working_days,
        company_limit=company_limit,
        company_count=company_count,
        company_slots_remaining=max(company_limit - company_count, 0),
        effective_day_value_cents=effective_day_value_cents(business.plan),
        trial_days=trial_days,
        revenue_share_percent=REVENUE_SHARE_PERCENT,
        ad_spend_take_percent=AD_SPEND_TAKE_PERCENT,
        stripe_fee_percent=STRIPE_FEE_PERCENT,
        stripe_fee_fixed_cents=STRIPE_FEE_FIXED_CENTS,
        can_start_paid_trial=business.plan == "preview",
        subscription_required_for_speed=business.plan == "preview",
        day_packs=[
            CreditPackStatus(
                key=pack.key,
                name=pack.name,
                working_days=pack.working_days,
                price_cents=pack.price_cents,
            )
            for pack in DAY_PACK_POLICIES.values()
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
