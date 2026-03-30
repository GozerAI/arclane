# Stripe Connect Sample Integration

A complete Stripe Connect integration sample for Arclane, demonstrating:

- **Connected account creation** using the V2 API
- **Account onboarding** via Account Links
- **Product management** on connected accounts
- **Public storefront** with direct charges and application fees
- **Platform subscriptions** charged to connected accounts
- **Billing portal** for subscription management
- **Webhook handling** for both thin events (V2) and standard events

## Quick Start

### 1. Install dependencies

```bash
cd examples/stripe-connect-sample
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Where to get it |
|----------|----------------|
| `STRIPE_SECRET_KEY` | [Dashboard > API keys](https://dashboard.stripe.com/apikeys) |
| `STRIPE_WEBHOOK_SECRET_CONNECT` | Created when setting up thin-event webhook |
| `STRIPE_WEBHOOK_SECRET_SUBSCRIPTIONS` | Created when setting up subscription webhook |
| `PLATFORM_PRICE_ID` | [Dashboard > Products](https://dashboard.stripe.com/products) — create a subscription product, copy the price ID |
| `BASE_URL` | `http://localhost:8080` for local dev |

### 3. Run the application

```bash
uvicorn app:app --port 8080 --reload
```

Open http://localhost:8080 in your browser.

### 4. Set up webhook listeners (for local development)

In a separate terminal, run the Stripe CLI listeners:

**Thin events (V2 account changes):**

```bash
stripe listen \
  --thin-events 'v2.core.account[requirements].updated,v2.core.account[configuration.merchant].capability_status_updated,v2.core.account[configuration.customer].capability_status_updated' \
  --forward-thin-to http://localhost:8080/webhooks/connect
```

**Standard events (subscriptions):**

```bash
stripe listen \
  --forward-to http://localhost:8080/webhooks/subscriptions
```

Copy the webhook signing secrets from the CLI output into your `.env` file.

## Production Webhook Setup

### Thin events (V2 account changes)

1. Go to [Stripe Dashboard > Developers > Webhooks](https://dashboard.stripe.com)
2. Click **+ Add destination**
3. In **Events from**, select **Connected accounts**
4. Click **Show advanced options**, select **Thin** payload style
5. Search for "v2" and select:
   - `v2.account[requirements].updated`
   - `v2.account[configuration.merchant].capability_status_updated`
   - `v2.account[configuration.customer].capability_status_updated`
6. Set the endpoint URL to `https://your-domain.com/webhooks/connect`

### Standard events (subscriptions)

1. Click **+ Add destination** again
2. Set the endpoint URL to `https://your-domain.com/webhooks/subscriptions`
3. Select events:
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.paid`
   - `invoice.payment_failed`

## Application Flow

```
Dashboard (/)
  |
  +-- Create Account --> POST /accounts
  |                        |
  |                        v
  +-- Account Detail (/accounts/{id})
        |
        +-- Onboard --> POST /accounts/{id}/onboarding --> Stripe hosted
        |
        +-- Products --> GET /accounts/{id}/products
        |     |
        |     +-- Create --> POST /accounts/{id}/products
        |
        +-- Storefront --> GET /storefront/{id}
        |     |
        |     +-- Buy --> POST /storefront/{id}/checkout --> Stripe Checkout
        |
        +-- Ad Budget --> GET /accounts/{id}/ads
        |     |
        |     +-- Fund --> POST /accounts/{id}/ads/fund --> Stripe Checkout
        |     |              (7.5% platform fee, balance credited on success)
        |     +-- Success --> GET /accounts/{id}/ads/success
        |
        +-- Subscribe --> POST /accounts/{id}/subscribe --> Stripe Checkout
        |
        +-- Billing Portal --> POST /accounts/{id}/billing-portal --> Stripe Portal
```

## Key Concepts

### V2 Accounts
The V2 API uses `dashboard`, `configuration`, and `defaults` instead of the legacy `type` parameter. A single account ID (`acct_...`) serves as both the connected account (merchant) and the customer (for platform subscriptions).

### Direct Charges
Payments are created directly on the connected account using the `Stripe-Account` header. The platform collects an `application_fee_amount` on each charge.

### Thin Events
V2 resources use thin events — lightweight notifications that contain only the event type and ID. You must fetch the full event data with a separate API call.

### Platform Subscriptions
The platform bills connected accounts using `customer_account` in the Checkout Session. This uses the V2 account's dual identity as both merchant and customer.
