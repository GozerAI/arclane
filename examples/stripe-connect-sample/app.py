"""
Stripe Connect Sample Integration for Arclane
==============================================
Demonstrates the full Stripe Connect lifecycle:

1. Creating connected accounts (V2 API)
2. Onboarding via Account Links
3. Listening for account requirement changes (thin events)
4. Creating products on connected accounts
5. Displaying a per-account storefront
6. Processing direct charges with application fees
7. Charging subscriptions to connected accounts
8. Billing portal for subscription management
9. Subscription webhook handling (standard events)

Run with:
    uvicorn app:app --port 8080 --reload

Prerequisites:
    pip install -r requirements.txt
    cp .env.example .env   # then fill in your keys
"""

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from stripe import StripeClient

# ─────────────────────────────────────────────────────────────
# 1. CONFIGURATION
# ─────────────────────────────────────────────────────────────
# Load environment variables from .env file in the same directory.
load_dotenv(Path(__file__).parent / ".env")

# STRIPE_SECRET_KEY: Your platform's secret key from
# https://dashboard.stripe.com/apikeys
# Use sk_test_... for testing, sk_live_... for production.
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
if not STRIPE_SECRET_KEY:
    raise RuntimeError(
        "\n\n"
        "  STRIPE_SECRET_KEY is not set.\n"
        "  1. Go to https://dashboard.stripe.com/apikeys\n"
        "  2. Copy your Secret key (starts with sk_test_ or sk_live_)\n"
        "  3. Set it in your .env file: STRIPE_SECRET_KEY=sk_test_...\n"
    )

# STRIPE_WEBHOOK_SECRET_CONNECT: Signing secret for the thin-events
# webhook endpoint (V2 account requirement changes).
# You get this when you create the webhook endpoint in Dashboard or
# from `stripe listen --thin-events ... --forward-thin-to ...`
STRIPE_WEBHOOK_SECRET_CONNECT = os.environ.get(
    "STRIPE_WEBHOOK_SECRET_CONNECT", ""
)
if not STRIPE_WEBHOOK_SECRET_CONNECT:
    print(
        "  WARNING: STRIPE_WEBHOOK_SECRET_CONNECT not set.\n"
        "  Thin-event webhook signature verification is disabled.\n"
        "  Set this before going to production."
    )

# STRIPE_WEBHOOK_SECRET_SUBSCRIPTIONS: Signing secret for the
# standard subscription webhook endpoint.
STRIPE_WEBHOOK_SECRET_SUBSCRIPTIONS = os.environ.get(
    "STRIPE_WEBHOOK_SECRET_SUBSCRIPTIONS", ""
)
if not STRIPE_WEBHOOK_SECRET_SUBSCRIPTIONS:
    print(
        "  WARNING: STRIPE_WEBHOOK_SECRET_SUBSCRIPTIONS not set.\n"
        "  Subscription webhook signature verification is disabled.\n"
        "  Set this before going to production."
    )

# PLATFORM_PRICE_ID: The Stripe Price ID for the platform subscription
# product that connected accounts subscribe to. Create one at
# https://dashboard.stripe.com/products then copy the price ID (price_...).
# This is used for the "Subscribe" flow on the account dashboard.
PLATFORM_PRICE_ID = os.environ.get("PLATFORM_PRICE_ID", "")
if not PLATFORM_PRICE_ID:
    print(
        "  WARNING: PLATFORM_PRICE_ID not set.\n"
        "  Subscription checkout will not work.\n"
        "  Create a product+price in your Stripe Dashboard, then set\n"
        "  PLATFORM_PRICE_ID=price_... in your .env file."
    )

# BASE_URL: The publicly-reachable URL of this application.
# Used for Stripe redirect URLs (success, cancel, refresh, return).
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")

# APPLICATION_FEE_PERCENT: The percentage the platform takes on each
# direct charge through the storefront.
APPLICATION_FEE_PERCENT = 15

# AD_SPEND_TAKE_PERCENT: The percentage the platform takes on ad
# budget top-ups. When a business owner funds their ad wallet,
# the platform keeps this cut and the rest goes toward ad spend.
# Matches Arclane's billing policy (AD_SPEND_TAKE_PERCENT = 7.5).
AD_SPEND_TAKE_PERCENT = 7.5

# ─────────────────────────────────────────────────────────────
# 2. STRIPE CLIENT
# ─────────────────────────────────────────────────────────────
# Create a single StripeClient instance. The SDK automatically uses
# the latest API version (2026-03-25.dahlia) — no need to set it.
# All Stripe API calls go through this client.
stripe_client = StripeClient(STRIPE_SECRET_KEY)


# ─────────────────────────────────────────────────────────────
# 3. DATABASE SETUP (SQLite for this sample)
# ─────────────────────────────────────────────────────────────
# In production, use your application's existing database (PostgreSQL,
# etc.) and ORM. This sample uses SQLite for zero-dependency setup.
DB_PATH = Path(__file__).parent / "data.db"


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        -- Maps platform users to their Stripe connected account IDs.
        -- In a real app, add a foreign key to your users table.
        CREATE TABLE IF NOT EXISTS connected_accounts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_account_id TEXT UNIQUE NOT NULL,
            display_name    TEXT NOT NULL,
            email           TEXT NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Tracks subscription status for connected accounts.
        -- Updated via subscription webhooks.
        CREATE TABLE IF NOT EXISTS subscriptions (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_account_id       TEXT NOT NULL,
            stripe_subscription_id  TEXT,
            status                  TEXT DEFAULT 'inactive',
            price_id                TEXT,
            updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stripe_account_id)
                REFERENCES connected_accounts(stripe_account_id)
        );

        -- Tracks prepaid ad budget for connected accounts.
        -- Funded via checkout, debited as ad campaigns run.
        CREATE TABLE IF NOT EXISTS ad_wallets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_account_id   TEXT UNIQUE NOT NULL,
            balance_cents       INTEGER DEFAULT 0,
            total_funded_cents  INTEGER DEFAULT 0,
            total_spent_cents   INTEGER DEFAULT 0,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stripe_account_id)
                REFERENCES connected_accounts(stripe_account_id)
        );

        -- Individual ad budget top-up and spend transactions.
        CREATE TABLE IF NOT EXISTS ad_transactions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_account_id   TEXT NOT NULL,
            type                TEXT NOT NULL,  -- 'topup' or 'spend'
            amount_cents        INTEGER NOT NULL,
            platform_fee_cents  INTEGER DEFAULT 0,
            description         TEXT,
            stripe_session_id   TEXT,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stripe_account_id)
                REFERENCES connected_accounts(stripe_account_id)
        );
        """
    )
    conn.close()


def get_db():
    """Return a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────
# 4. FASTAPI APPLICATION
# ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize the database on startup."""
    init_db()
    yield


app = FastAPI(
    title="Arclane Stripe Connect Sample",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ─────────────────────────────────────────────────────────────
# 5. DASHBOARD — List & Create Connected Accounts
# ─────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """
    Main dashboard: shows all connected accounts and a form to create
    a new one. Each account links to its detail page.
    """
    db = get_db()
    accounts = db.execute(
        "SELECT * FROM connected_accounts ORDER BY created_at DESC"
    ).fetchall()
    db.close()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "accounts": accounts}
    )


@app.post("/accounts")
async def create_account(
    display_name: str = Form(...),
    email: str = Form(...),
):
    """
    Create a new Stripe connected account using the V2 API.

    Important:
    - Do NOT pass `type` at the top level (no 'express', 'standard',
      or 'custom'). The V2 API uses `dashboard` and `configuration`
      instead.
    - `dashboard: 'full'` gives the connected account access to the
      full Stripe Dashboard.
    - `defaults.responsibilities` sets who is responsible for fees
      and losses — here we let Stripe handle both.
    - `configuration.merchant` enables card payment capabilities.
    - `configuration.customer` is required for subscription billing
      where the connected account is the customer.
    """
    # --- Create the V2 connected account via Stripe ---
    account = stripe_client.v2.core.accounts.create(
        {
            "display_name": display_name,
            "contact_email": email,
            # The country where the connected account operates.
            "identity": {
                "country": "us",
            },
            # 'full' gives the user access to the Stripe Dashboard.
            # Use 'none' if you want to build your own dashboard.
            "dashboard": "full",
            # Platform defaults: Stripe collects fees and covers losses.
            "defaults": {
                "responsibilities": {
                    "fees_collector": "stripe",
                    "losses_collector": "stripe",
                },
            },
            # Configuration determines what the account can do.
            "configuration": {
                # customer: {} enables the account to be used as a
                # customer (for platform subscriptions).
                "customer": {},
                # merchant: enables receiving payments.
                "merchant": {
                    "capabilities": {
                        "card_payments": {
                            "requested": True,
                        },
                    },
                },
            },
        }
    )

    # --- Store the account mapping in our database ---
    db = get_db()
    db.execute(
        "INSERT INTO connected_accounts (stripe_account_id, display_name, email) "
        "VALUES (?, ?, ?)",
        (account.id, display_name, email),
    )
    db.commit()
    db.close()

    # Redirect to the account detail page where they can onboard.
    return RedirectResponse(
        url=f"/accounts/{account.id}", status_code=303
    )


# ─────────────────────────────────────────────────────────────
# 6. ACCOUNT DETAIL — Onboarding Status & Actions
# ─────────────────────────────────────────────────────────────


@app.get("/accounts/{account_id}", response_class=HTMLResponse)
async def account_detail(request: Request, account_id: str):
    """
    Account detail page showing:
    - Onboarding status (fetched live from Stripe, not cached)
    - Button to start/resume onboarding
    - Product management link
    - Subscription status and actions
    """
    # --- Look up the account in our database ---
    db = get_db()
    local_account = db.execute(
        "SELECT * FROM connected_accounts WHERE stripe_account_id = ?",
        (account_id,),
    ).fetchone()
    if not local_account:
        raise HTTPException(status_code=404, detail="Account not found")

    # --- Fetch the latest account status from Stripe ---
    # Always retrieve fresh from the API so we see the real-time
    # onboarding and capability status. The `include` parameter
    # fetches nested configuration and requirements data.
    stripe_account = stripe_client.v2.core.accounts.retrieve(
        account_id,
        {"include": ["configuration.merchant", "requirements"]},
    )

    # --- Determine onboarding and payment readiness ---
    # card_payments capability status tells us if the account can
    # accept payments.
    ready_to_process = False
    try:
        card_status = (
            stripe_account.configuration.merchant.capabilities.card_payments.status
        )
        ready_to_process = card_status == "active"
    except (AttributeError, TypeError):
        pass

    # Requirements summary tells us if onboarding is complete.
    # If there are no "currently_due" or "past_due" requirements,
    # the account has finished onboarding.
    onboarding_complete = False
    try:
        req_status = (
            stripe_account.requirements.summary.minimum_deadline.status
        )
        onboarding_complete = (
            req_status != "currently_due" and req_status != "past_due"
        )
    except (AttributeError, TypeError):
        # If requirements aren't available yet, onboarding isn't done.
        pass

    # --- Look up subscription status from our database ---
    subscription = db.execute(
        "SELECT * FROM subscriptions WHERE stripe_account_id = ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    db.close()

    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "account": dict(local_account),
            "stripe_account_id": account_id,
            "ready_to_process": ready_to_process,
            "onboarding_complete": onboarding_complete,
            "subscription": dict(subscription) if subscription else None,
            "platform_price_id": PLATFORM_PRICE_ID,
        },
    )


# ─────────────────────────────────────────────────────────────
# 7. ONBOARDING — Account Links
# ─────────────────────────────────────────────────────────────


@app.post("/accounts/{account_id}/onboarding")
async def start_onboarding(account_id: str):
    """
    Generate a Stripe Account Link and redirect the user to it.

    Account Links are one-time-use URLs that take the connected
    account holder through Stripe's hosted onboarding flow where
    they provide identity verification, banking details, etc.

    - refresh_url: Where Stripe sends the user if the link expires
      or they need to restart. We send them back here to generate
      a fresh link.
    - return_url: Where Stripe sends the user after they complete
      (or exit) onboarding. We send them to the account detail page.
    - configurations: ['merchant', 'customer'] means we onboard
      them for both receiving payments and being a platform customer.
    """
    account_link = stripe_client.v2.core.account_links.create(
        {
            "account": account_id,
            "use_case": {
                "type": "account_onboarding",
                "account_onboarding": {
                    "configurations": ["merchant", "customer"],
                    # If the link expires, bring them back to regenerate.
                    "refresh_url": (
                        f"{BASE_URL}/accounts/{account_id}"
                    ),
                    # After onboarding, return to the account detail page.
                    "return_url": (
                        f"{BASE_URL}/accounts/{account_id}"
                        f"?accountId={account_id}"
                    ),
                },
            },
        }
    )

    # Redirect the user to Stripe's hosted onboarding.
    return RedirectResponse(url=account_link.url, status_code=303)


# ─────────────────────────────────────────────────────────────
# 8. PRODUCTS — Create & List on Connected Accounts
# ─────────────────────────────────────────────────────────────


@app.get("/accounts/{account_id}/products", response_class=HTMLResponse)
async def products_page(request: Request, account_id: str):
    """
    Product management page for a connected account.
    Lists existing products and provides a form to create new ones.

    Products are created ON the connected account (using the
    Stripe-Account header) so the connected account owns them.
    """
    # --- List products on the connected account ---
    # The second dict passes the stripe_account option which sets
    # the Stripe-Account header, scoping the request to that account.
    products = stripe_client.v1.products.list(
        {
            "limit": 20,
            "active": True,
            "expand": ["data.default_price"],
        },
        {"stripe_account": account_id},
    )

    return templates.TemplateResponse(
        "products.html",
        {
            "request": request,
            "account_id": account_id,
            "products": products.data,
        },
    )


@app.post("/accounts/{account_id}/products")
async def create_product(
    account_id: str,
    name: str = Form(...),
    description: str = Form(""),
    price: str = Form(...),
    currency: str = Form("usd"),
):
    """
    Create a product with a default price on the connected account.

    The product and its price are owned by the connected account.
    When customers purchase through the storefront, the payment goes
    to this connected account (minus the platform application fee).
    """
    # Convert dollar amount to cents (Stripe uses smallest currency unit).
    price_in_cents = int(float(price) * 100)

    # --- Create the product on the connected account ---
    stripe_client.v1.products.create(
        {
            "name": name,
            "description": description,
            # default_price_data creates a Price object automatically
            # and sets it as the product's default price.
            "default_price_data": {
                "unit_amount": price_in_cents,
                "currency": currency,
            },
        },
        # This sets the Stripe-Account header so the product is
        # created on the connected account, not the platform.
        {"stripe_account": account_id},
    )

    return RedirectResponse(
        url=f"/accounts/{account_id}/products", status_code=303
    )


# ─────────────────────────────────────────────────────────────
# 9. STOREFRONT — Public Product Display & Checkout
# ─────────────────────────────────────────────────────────────


@app.get("/storefront/{account_id}", response_class=HTMLResponse)
async def storefront(request: Request, account_id: str):
    """
    Public-facing storefront for a connected account's customers.

    NOTE: In production, use a slug, username, or subdomain instead
    of the raw Stripe account ID in the URL. The account ID is used
    here for simplicity in this sample.

    Example production URL: /store/janes-bakery
    """
    # --- Fetch products from the connected account ---
    products = stripe_client.v1.products.list(
        {
            "limit": 20,
            "active": True,
            "expand": ["data.default_price"],
        },
        {"stripe_account": account_id},
    )

    return templates.TemplateResponse(
        "storefront.html",
        {
            "request": request,
            "account_id": account_id,
            "products": products.data,
        },
    )


@app.post("/storefront/{account_id}/checkout")
async def create_checkout(
    account_id: str,
    product_name: str = Form(...),
    price_amount: int = Form(...),
    currency: str = Form("usd"),
):
    """
    Create a Stripe Checkout Session for a direct charge.

    Direct charges:
    - The payment is created directly on the connected account.
    - The platform takes an application fee (15% in this sample).
    - The customer sees the connected account's name on their
      card statement.
    - The Stripe-Account header routes the charge to the
      connected account.
    """
    # Calculate the platform's application fee.
    # For a $10 product (1000 cents) at 15%, the fee is 150 cents.
    application_fee = int(price_amount * APPLICATION_FEE_PERCENT / 100)

    # --- Create a Checkout Session on the connected account ---
    session = stripe_client.v1.checkout.sessions.create(
        {
            "line_items": [
                {
                    # price_data lets us create a one-off price inline
                    # without needing a pre-existing Price object.
                    "price_data": {
                        "currency": currency,
                        "unit_amount": price_amount,
                        "product_data": {
                            "name": product_name,
                        },
                    },
                    "quantity": 1,
                },
            ],
            # payment_intent_data configures the underlying PaymentIntent.
            # application_fee_amount is the amount (in cents) the platform
            # keeps from this payment.
            "payment_intent_data": {
                "application_fee_amount": application_fee,
            },
            "mode": "payment",
            # {CHECKOUT_SESSION_ID} is a Stripe template variable that
            # gets replaced with the actual session ID after payment.
            "success_url": (
                f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}"
            ),
            "cancel_url": f"{BASE_URL}/storefront/{account_id}",
        },
        # Route this charge to the connected account.
        {"stripe_account": account_id},
    )

    # Redirect the customer to Stripe's hosted checkout page.
    return RedirectResponse(url=session.url, status_code=303)


# ─────────────────────────────────────────────────────────────
# 10. AD BUDGET — Prepaid Wallet for Ad Spend
# ─────────────────────────────────────────────────────────────
#
# Business owners fund an ad wallet through a checkout session.
# The platform takes a 7.5% cut on every top-up. The remaining
# balance is available for running ad campaigns (Meta, Google,
# LinkedIn, Twitter/X) through Arclane's advertising module.
#
# Flow:
#   1. Business owner clicks "Fund Ad Budget" and enters an amount
#   2. Checkout session is created as a direct charge on the
#      connected account with a 7.5% application fee
#   3. On successful payment, the wallet balance is credited
#      (minus the platform fee)
#   4. As ad campaigns run, the wallet is debited
#
# This is a direct charge (like storefront purchases) because the
# ad budget payment goes TO the connected account's Stripe balance,
# with the platform skimming the fee. The platform then executes
# ad spend on behalf of the business using separate ad platform APIs.


@app.get("/accounts/{account_id}/ads", response_class=HTMLResponse)
async def ads_page(request: Request, account_id: str):
    """
    Ad budget management page for a connected account.
    Shows current wallet balance, transaction history, and a
    form to top up the ad budget.
    """
    db = get_db()

    # --- Get or create the ad wallet ---
    wallet = db.execute(
        "SELECT * FROM ad_wallets WHERE stripe_account_id = ?",
        (account_id,),
    ).fetchone()
    if not wallet:
        db.execute(
            "INSERT INTO ad_wallets (stripe_account_id) VALUES (?)",
            (account_id,),
        )
        db.commit()
        wallet = db.execute(
            "SELECT * FROM ad_wallets WHERE stripe_account_id = ?",
            (account_id,),
        ).fetchone()

    # --- Get recent transactions ---
    transactions = db.execute(
        "SELECT * FROM ad_transactions WHERE stripe_account_id = ? "
        "ORDER BY created_at DESC LIMIT 20",
        (account_id,),
    ).fetchall()

    db.close()

    return templates.TemplateResponse(
        "ads.html",
        {
            "request": request,
            "account_id": account_id,
            "wallet": dict(wallet),
            "transactions": [dict(t) for t in transactions],
            "ad_fee_percent": AD_SPEND_TAKE_PERCENT,
        },
    )


@app.post("/accounts/{account_id}/ads/fund")
async def fund_ad_budget(
    account_id: str,
    amount: str = Form(...),
):
    """
    Create a checkout session to fund the ad budget.

    This is a direct charge on the connected account — the payment
    lands in the connected account's Stripe balance. The platform
    takes a 7.5% application fee on the top-up.

    After successful payment, the webhook (or success callback)
    credits the ad wallet with the net amount (total - platform fee).

    Example: Business funds $100 ad budget
      - Total charge: $100.00 (10000 cents)
      - Platform fee: $7.50 (750 cents) at 7.5%
      - Ad wallet credit: $92.50 (9250 cents) available for ads
    """
    amount_cents = int(float(amount) * 100)
    if amount_cents < 500:  # Minimum $5.00
        raise HTTPException(
            status_code=400,
            detail="Minimum ad budget top-up is $5.00",
        )

    # Calculate the platform's cut on the ad budget top-up.
    platform_fee_cents = int(amount_cents * AD_SPEND_TAKE_PERCENT / 100)

    # --- Create a Checkout Session as a direct charge ---
    # The charge is on the connected account (stripe_account header).
    # The platform collects the application_fee_amount.
    session = stripe_client.v1.checkout.sessions.create(
        {
            "line_items": [
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": amount_cents,
                        "product_data": {
                            "name": "Ad Budget Top-Up",
                            "description": (
                                f"Fund advertising wallet. "
                                f"${amount_cents / 100:.2f} total, "
                                f"${platform_fee_cents / 100:.2f} platform fee, "
                                f"${(amount_cents - platform_fee_cents) / 100:.2f} "
                                f"available for ads."
                            ),
                        },
                    },
                    "quantity": 1,
                },
            ],
            "payment_intent_data": {
                # Platform keeps 7.5% as the application fee.
                "application_fee_amount": platform_fee_cents,
                # Store metadata so the webhook can credit the wallet.
                "metadata": {
                    "type": "ad_budget_topup",
                    "account_id": account_id,
                    "gross_amount_cents": str(amount_cents),
                    "platform_fee_cents": str(platform_fee_cents),
                    "net_credit_cents": str(
                        amount_cents - platform_fee_cents
                    ),
                },
            },
            "mode": "payment",
            # After payment, redirect to the ad budget success handler
            # which credits the wallet.
            "success_url": (
                f"{BASE_URL}/accounts/{account_id}/ads/success"
                f"?session_id={{CHECKOUT_SESSION_ID}}"
            ),
            "cancel_url": f"{BASE_URL}/accounts/{account_id}/ads",
        },
        # Direct charge — payment goes to the connected account.
        {"stripe_account": account_id},
    )

    return RedirectResponse(url=session.url, status_code=303)


@app.get("/accounts/{account_id}/ads/success")
async def ad_fund_success(account_id: str, session_id: str = ""):
    """
    Called after a successful ad budget checkout. Retrieves the
    session to confirm payment and credits the ad wallet.

    In production, you should ALSO handle this via webhooks
    (checkout.session.completed) for reliability — the customer
    might close their browser before reaching this page.
    """
    if not session_id:
        return RedirectResponse(
            url=f"/accounts/{account_id}/ads", status_code=303
        )

    # --- Retrieve the checkout session to get payment details ---
    session = stripe_client.v1.checkout.sessions.retrieve(
        session_id,
        {"expand": ["payment_intent"]},
        {"stripe_account": account_id},
    )

    # Only credit if the payment actually succeeded.
    if session.payment_status != "paid":
        return RedirectResponse(
            url=f"/accounts/{account_id}/ads", status_code=303
        )

    # Extract amounts from the payment intent metadata.
    metadata = session.payment_intent.metadata if session.payment_intent else {}
    gross_cents = int(metadata.get("gross_amount_cents", 0))
    fee_cents = int(metadata.get("platform_fee_cents", 0))
    net_cents = int(metadata.get("net_credit_cents", 0))

    if net_cents <= 0:
        return RedirectResponse(
            url=f"/accounts/{account_id}/ads", status_code=303
        )

    # --- Credit the ad wallet ---
    db = get_db()

    # Check if this session was already processed (idempotency).
    existing = db.execute(
        "SELECT id FROM ad_transactions WHERE stripe_session_id = ?",
        (session_id,),
    ).fetchone()

    if not existing:
        # Credit the wallet balance.
        db.execute(
            "UPDATE ad_wallets SET "
            "balance_cents = balance_cents + ?, "
            "total_funded_cents = total_funded_cents + ?, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE stripe_account_id = ?",
            (net_cents, gross_cents, account_id),
        )
        # Record the transaction.
        db.execute(
            "INSERT INTO ad_transactions "
            "(stripe_account_id, type, amount_cents, platform_fee_cents, "
            " description, stripe_session_id) "
            "VALUES (?, 'topup', ?, ?, ?, ?)",
            (
                account_id,
                net_cents,
                fee_cents,
                f"Ad budget top-up: ${gross_cents / 100:.2f} "
                f"(${fee_cents / 100:.2f} platform fee)",
                session_id,
            ),
        )
        db.commit()

    db.close()

    return RedirectResponse(
        url=f"/accounts/{account_id}/ads", status_code=303
    )


# ─────────────────────────────────────────────────────────────
# 11. SUBSCRIPTIONS — Charge Connected Accounts
# (was section 10 — renumbered after adding ad budget)
# ─────────────────────────────────────────────────────────────


@app.post("/accounts/{account_id}/subscribe")
async def subscribe_account(account_id: str):
    """
    Create a subscription checkout for a connected account.

    With V2 accounts, the same account ID (acct_...) serves as both
    the connected account AND the customer. We use `customer_account`
    in the checkout session to bill the connected account directly.

    This is a PLATFORM-LEVEL call (no stripe_account header) because
    the platform is creating the subscription, not the connected account.
    """
    if not PLATFORM_PRICE_ID:
        raise HTTPException(
            status_code=500,
            detail=(
                "PLATFORM_PRICE_ID not configured. "
                "Create a subscription product in your Stripe Dashboard "
                "and set PLATFORM_PRICE_ID in .env."
            ),
        )

    session = stripe_client.v1.checkout.sessions.create(
        {
            # customer_account: the V2 connected account ID.
            # This tells Stripe to bill this account as a customer.
            "customer_account": account_id,
            "mode": "subscription",
            "line_items": [
                {
                    "price": PLATFORM_PRICE_ID,
                    "quantity": 1,
                },
            ],
            "success_url": (
                f"{BASE_URL}/accounts/{account_id}"
                f"?session_id={{CHECKOUT_SESSION_ID}}"
            ),
            "cancel_url": f"{BASE_URL}/accounts/{account_id}",
        }
    )

    return RedirectResponse(url=session.url, status_code=303)


# ─────────────────────────────────────────────────────────────
# 11. BILLING PORTAL — Subscription Management
# ─────────────────────────────────────────────────────────────


@app.post("/accounts/{account_id}/billing-portal")
async def billing_portal(account_id: str):
    """
    Create a Billing Portal session for a connected account to
    manage their subscription (upgrade, downgrade, cancel, update
    payment method).

    Like the subscription checkout, this uses `customer_account`
    instead of a customer ID because V2 accounts serve as both.
    """
    session = stripe_client.v1.billing_portal.sessions.create(
        {
            # customer_account: the connected account managing
            # their own subscription.
            "customer_account": account_id,
            # Where to send the user when they're done in the portal.
            "return_url": f"{BASE_URL}/accounts/{account_id}",
        }
    )

    return RedirectResponse(url=session.url, status_code=303)


# ─────────────────────────────────────────────────────────────
# 13. SUCCESS PAGE
# ─────────────────────────────────────────────────────────────


@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request, session_id: str = ""):
    """
    Generic success page shown after a checkout completes.
    Displays the session ID for reference.
    """
    return templates.TemplateResponse(
        "success.html",
        {"request": request, "session_id": session_id},
    )


# ─────────────────────────────────────────────────────────────
# 14. WEBHOOKS — Thin Events (V2 Account Changes)
# ─────────────────────────────────────────────────────────────
#
# Thin events are lightweight notifications for V2 API resources.
# They contain only the event type and a reference — you must
# fetch the full event data separately.
#
# To set up locally, run:
#   stripe listen \
#     --thin-events 'v2.core.account[requirements].updated,
#       v2.core.account[configuration.merchant].capability_status_updated,
#       v2.core.account[configuration.customer].capability_status_updated' \
#     --forward-thin-to http://localhost:8080/webhooks/connect
#
# To set up in production:
#   1. Go to Stripe Dashboard > Developers > Webhooks
#   2. Click "+ Add destination"
#   3. In "Events from", select "Connected accounts"
#   4. Click "Show advanced options", select "Thin" payload style
#   5. Search for v2 events and select:
#      - v2.account[requirements].updated
#      - v2.account[configuration.merchant].capability_status_updated
#      - v2.account[configuration.customer].capability_status_updated


@app.post("/webhooks/connect")
async def webhook_connect(request: Request):
    """
    Handle thin events for V2 connected account changes.

    These events fire when:
    - Account requirements change (new docs needed, verification
      updates, regulatory changes)
    - Capability status changes (card_payments activated/deactivated)
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # --- Step 1: Parse the thin event ---
    # parse_thin_event verifies the webhook signature and returns
    # a lightweight event object with just the type and ID.
    try:
        thin_event = stripe_client.parse_thin_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET_CONNECT
        )
    except Exception as e:
        print(f"  Webhook signature verification failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    # --- Step 2: Fetch the full event data ---
    # The thin event only has the type and ID. We need to retrieve
    # the full event to see what actually changed.
    try:
        event = stripe_client.v2.core.events.retrieve(thin_event.id)
    except Exception as e:
        print(f"  Failed to retrieve event {thin_event.id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # --- Step 3: Handle each event type ---
    event_type = thin_event.type

    if event_type == "v2.core.account[requirements].updated":
        # Requirements changed — the account may need to provide
        # additional information. In production, you would:
        # - Notify the account holder via email/dashboard
        # - Update your database with the new requirements status
        # - Possibly pause payouts if requirements are past_due
        print(f"  Requirements updated for account event: {event.id}")
        # TODO: Notify the user that their requirements have changed.
        # TODO: Update your database with new requirements status.

    elif event_type == (
        "v2.core.account[configuration.merchant]"
        ".capability_status_updated"
    ):
        # Merchant capability status changed — e.g., card_payments
        # went from 'pending' to 'active' or was disabled.
        print(f"  Merchant capability changed for event: {event.id}")
        # TODO: Update your database to reflect whether the account
        # can accept payments.
        # TODO: Enable/disable the storefront based on capability status.

    elif event_type == (
        "v2.core.account[configuration.customer]"
        ".capability_status_updated"
    ):
        # Customer capability status changed — affects whether this
        # account can be billed as a customer (subscriptions).
        print(f"  Customer capability changed for event: {event.id}")
        # TODO: Update subscription eligibility in your database.

    else:
        print(f"  Unhandled thin event type: {event_type}")

    # Always return 200 to acknowledge receipt. Stripe will retry
    # on non-2xx responses.
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────
# 15. WEBHOOKS — Standard Events (Subscriptions)
# ─────────────────────────────────────────────────────────────
#
# Subscription lifecycle events use standard (non-thin) webhooks.
# These contain the full event payload.
#
# To set up locally, run:
#   stripe listen --forward-to http://localhost:8080/webhooks/subscriptions
#
# To set up in production:
#   1. Go to Stripe Dashboard > Developers > Webhooks
#   2. Click "+ Add destination"
#   3. Add URL: https://your-domain.com/webhooks/subscriptions
#   4. Select events:
#      - customer.subscription.created
#      - customer.subscription.updated
#      - customer.subscription.deleted
#      - invoice.paid
#      - invoice.payment_failed


@app.post("/webhooks/subscriptions")
async def webhook_subscriptions(request: Request):
    """
    Handle standard webhook events for subscription lifecycle.

    These events tell you when subscriptions are created, changed,
    or canceled so you can update access in your application.
    """
    import stripe

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # --- Step 1: Verify the webhook signature ---
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET_SUBSCRIPTIONS
        )
    except Exception as e:
        print(f"  Subscription webhook verification failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    event_type = event["type"]
    data = event["data"]["object"]

    # --- Step 2: Handle each event type ---

    if event_type == "customer.subscription.created":
        # A new subscription was created. Store it in the database.
        # For V2 accounts, use .customer_account (acct_...) not
        # .customer (cus_...) to identify the connected account.
        account_id = data.get("customer_account", "")
        subscription_id = data.get("id", "")
        status = data.get("status", "")
        price_id = ""
        if data.get("items", {}).get("data"):
            price_id = data["items"]["data"][0].get("price", {}).get(
                "id", ""
            )

        print(
            f"  Subscription created: {subscription_id} "
            f"for account {account_id} (status: {status})"
        )

        # TODO: Store the subscription in your database.
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO subscriptions "
            "(stripe_account_id, stripe_subscription_id, status, price_id) "
            "VALUES (?, ?, ?, ?)",
            (account_id, subscription_id, status, price_id),
        )
        db.commit()
        db.close()

    elif event_type == "customer.subscription.updated":
        # Subscription was updated — could be an upgrade, downgrade,
        # cancellation scheduled, or reactivation.
        account_id = data.get("customer_account", "")
        subscription_id = data.get("id", "")
        status = data.get("status", "")
        cancel_at_period_end = data.get("cancel_at_period_end", False)

        # Check if this is a cancellation at period end.
        if cancel_at_period_end:
            print(
                f"  Subscription {subscription_id} scheduled for "
                f"cancellation at period end"
            )
        # Check if a canceled subscription was reactivated.
        elif not cancel_at_period_end and status == "active":
            print(f"  Subscription {subscription_id} reactivated")

        # Check for plan changes (upgrades/downgrades).
        price_id = ""
        if data.get("items", {}).get("data"):
            price_id = data["items"]["data"][0].get("price", {}).get(
                "id", ""
            )
            print(
                f"  Subscription {subscription_id} now on "
                f"price {price_id}"
            )

        # Check for paused collections.
        pause = data.get("pause_collection")
        if pause:
            resumes = pause.get("resumes_at")
            print(
                f"  Subscription {subscription_id} paused, "
                f"resumes at {resumes}"
            )
        elif pause is None:
            # pause_collection is empty — subscription resumed.
            pass

        # TODO: Update access based on the new subscription state.
        db = get_db()
        db.execute(
            "UPDATE subscriptions SET status = ?, price_id = ?, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE stripe_account_id = ?",
            (status, price_id, account_id),
        )
        db.commit()
        db.close()

    elif event_type == "customer.subscription.deleted":
        # Subscription was fully canceled. Revoke access.
        account_id = data.get("customer_account", "")
        subscription_id = data.get("id", "")

        print(
            f"  Subscription deleted: {subscription_id} "
            f"for account {account_id}"
        )

        # TODO: Revoke access to the subscribed product/service.
        db = get_db()
        db.execute(
            "UPDATE subscriptions SET status = 'canceled', "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE stripe_account_id = ?",
            (account_id,),
        )
        db.commit()
        db.close()

    elif event_type == "invoice.paid":
        # An invoice was paid. This confirms a subscription payment
        # went through successfully.
        account_id = data.get("customer_account", "")
        invoice_id = data.get("id", "")
        amount = data.get("amount_paid", 0)
        print(
            f"  Invoice paid: {invoice_id} for account {account_id} "
            f"(amount: {amount})"
        )
        # TODO: Record the payment, send a receipt, update access dates.

    elif event_type == "invoice.payment_failed":
        # A subscription invoice payment failed. The customer may need
        # to update their payment method.
        account_id = data.get("customer_account", "")
        invoice_id = data.get("id", "")
        print(
            f"  Invoice payment failed: {invoice_id} "
            f"for account {account_id}"
        )
        # TODO: Notify the user to update their payment method.
        # TODO: Consider grace periods before revoking access.

    elif event_type == "payment_method.attached":
        print(f"  Payment method attached: {data.get('id')}")

    elif event_type == "payment_method.detached":
        print(f"  Payment method detached: {data.get('id')}")

    elif event_type == "customer.updated":
        # Check for default payment method changes.
        default_pm = (
            data.get("invoice_settings", {}).get("default_payment_method")
        )
        print(
            f"  Customer updated: {data.get('id')}, "
            f"default PM: {default_pm}"
        )
        # NOTE: Do not use customer billing email as login credentials.

    else:
        print(f"  Unhandled subscription event: {event_type}")

    return {"status": "ok"}
