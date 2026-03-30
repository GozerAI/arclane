"""Stripe Connect integration — platform payments with revenue sharing.

Arclane acts as the platform. Business owners connect their Stripe accounts
and receive payments from their customers, minus the Arclane platform fee.

Fee structure:
- 15% on product sales (purchases through subdomain checkout)
- 5% on other pass-through transactions (subscriptions, services, etc.)
"""

import httpx

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("stripe_connect")

STRIPE_API = "https://api.stripe.com/v1"

# Fee percentages (basis points for Stripe)
SALE_FEE_PERCENT = 15  # 15% on product sales
OTHER_FEE_PERCENT = 5  # 5% on other transactions


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.stripe_secret_key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def create_connect_account(business_slug: str, owner_email: str) -> str:
    """Create a Stripe Connect Express account for a business owner.

    Returns the Stripe account ID (acct_xxx).
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRIPE_API}/accounts",
            headers=_headers(),
            data={
                "type": "express",
                "email": owner_email,
                "metadata[arclane_slug]": business_slug,
                "capabilities[card_payments][requested]": "true",
                "capabilities[transfers][requested]": "true",
            },
            timeout=15.0,
        )
        resp.raise_for_status()

    account = resp.json()
    account_id = account["id"]
    log.info("Created Connect account %s for %s", account_id, business_slug)
    return account_id


async def create_onboarding_link(
    account_id: str,
    return_url: str,
    refresh_url: str,
) -> str:
    """Generate a Stripe Connect onboarding URL.

    The business owner visits this URL to complete identity verification
    and bank account setup. Stripe hosts the entire flow.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRIPE_API}/account_links",
            headers=_headers(),
            data={
                "account": account_id,
                "type": "account_onboarding",
                "return_url": return_url,
                "refresh_url": refresh_url,
            },
            timeout=15.0,
        )
        resp.raise_for_status()

    link = resp.json()
    return link["url"]


async def create_checkout_session(
    connected_account_id: str,
    product_name: str,
    amount_cents: int,
    currency: str = "usd",
    success_url: str = "",
    cancel_url: str = "",
    transaction_type: str = "sale",
    metadata: dict | None = None,
) -> dict:
    """Create a Stripe Checkout session with platform fee.

    The payment goes to the connected account, minus the Arclane platform fee.

    Args:
        transaction_type: "sale" (15% fee) or "other" (5% fee)
    """
    fee_percent = SALE_FEE_PERCENT if transaction_type == "sale" else OTHER_FEE_PERCENT
    fee_amount = int(amount_cents * fee_percent / 100)

    data = {
        "mode": "payment",
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][product_data][name]": product_name,
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][quantity]": "1",
        "payment_intent_data[application_fee_amount]": str(fee_amount),
        "payment_intent_data[transfer_data][destination]": connected_account_id,
        "success_url": success_url or "https://arclane.cloud/checkout/success",
        "cancel_url": cancel_url or "https://arclane.cloud/checkout/cancel",
    }

    if metadata:
        for k, v in metadata.items():
            data[f"metadata[{k}]"] = str(v)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRIPE_API}/checkout/sessions",
            headers=_headers(),
            data=data,
            timeout=15.0,
        )
        resp.raise_for_status()

    session = resp.json()
    log.info(
        "Checkout session %s: %s cents, %d%% fee (%d cents) → %s",
        session["id"], amount_cents, fee_percent, fee_amount, connected_account_id,
    )
    return {"session_id": session["id"], "url": session["url"]}


async def get_account_status(account_id: str) -> dict:
    """Check if a connected account has completed onboarding."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRIPE_API}/accounts/{account_id}",
            headers=_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()

    account = resp.json()
    return {
        "id": account["id"],
        "charges_enabled": account.get("charges_enabled", False),
        "payouts_enabled": account.get("payouts_enabled", False),
        "onboarded": account.get("charges_enabled", False) and account.get("details_submitted", False),
    }


async def create_login_link(account_id: str) -> str:
    """Generate a link to the connected account's Stripe Express dashboard."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{STRIPE_API}/accounts/{account_id}/login_links",
            headers=_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()

    return resp.json()["url"]
