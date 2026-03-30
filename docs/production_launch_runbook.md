# Arclane Production Launch Runbook

This is the shortest path from the current repo state to a live first-customer deployment.

## What is already wired

- Apex app at `arclane.cloud`
- Wildcard tenant routing at `*.arclane.cloud`
- Session-based auth
- Website intake with SSRF guardrails
- Internal queue-based operating plan
- Add-on checkout flow through Vinzy and Stripe metadata passthrough
- Add-on purchase callbacks that queue the purchased package automatically
- VPS smoke test script
- Docker Compose with Postgres, Caddy, and app services

## Required production inputs

You will need these before the final deploy:

- VPS IP address
- DNS control for `arclane.cloud`
- Google OAuth client ID / secret
- GitHub OAuth client ID / secret
- Vinzy base URL and service token
- Stripe enabled in Vinzy
- Resend API key, or your chosen outbound email provider details
- LLM endpoint details if running in `internal` mode with a paid model
- A production `ARCLANE_SECRET_KEY`
- A production `ARCLANE_WEBHOOK_SIGNING_SECRET`
- Postgres password

## Environment values to fill in

Edit `.env` on the VPS and set at minimum:

- `POSTGRES_PASSWORD`
- `ARCLANE_DATABASE_URL`
- `ARCLANE_SECRET_KEY`
- `ARCLANE_WEBHOOK_SIGNING_SECRET`
- `ARCLANE_ZUUL_SERVICE_TOKEN`
- `ARCLANE_VINZY_BASE_URL`
- `ARCLANE_STRIPE_ENABLED=true`
- `ARCLANE_RESEND_API_KEY`
- `ARCLANE_GOOGLE_CLIENT_ID`
- `ARCLANE_GOOGLE_CLIENT_SECRET`
- `ARCLANE_GITHUB_CLIENT_ID`
- `ARCLANE_GITHUB_CLIENT_SECRET`
- `ARCLANE_LLM_BASE_URL`
- `ARCLANE_LLM_API_KEY`
- `ARCLANE_LLM_MODEL`

Recommended production defaults:

- `ARCLANE_ENV=production`
- `ARCLANE_DOMAIN=arclane.cloud`
- `ARCLANE_WORKSPACES_ROOT=/var/arclane/workspaces`
- `ARCLANE_PUBLIC_LIVE_FEED_IDENTITY=false`
- `ARCLANE_PUBLIC_LIVE_FEED_DETAIL=false`

## DNS

Set these DNS records:

- `A arclane.cloud -> <VPS_IP>`
- `A *.arclane.cloud -> <VPS_IP>`

If mail remains outbound-only for launch, also set the provider-required records for the sending domain.

## OAuth callback URLs

Configure these provider callback URLs:

- Google: `https://arclane.cloud/api/auth/callback/google`
- GitHub: `https://arclane.cloud/api/auth/callback/github`

## Vinzy / Stripe requirements

Vinzy must support these checkout product codes:

- `ARC` for subscriptions
- `ARC-CREDITS` for credit packs
- `ARC-ADDON` for add-ons

Vinzy must pass metadata through checkout and callbacks, including:

- `business_slug`
- `customer_email`
- `checkout_kind`
- `credit_pack` when relevant
- `add_on` when relevant

Arclane expects either:

- `/api/businesses/{slug}/billing/webhook` with `event=add_on.purchased`

or

- `/api/billing/provision-complete` with:
  - `metadata.checkout_kind=add_on`
  - `metadata.add_on=<key>`

## Mail decision

Launch-safe option:

- Keep outbound email through Resend
- Keep product language at `business address configured`
- Do not market a full inbox until inbound routing is proven

If you want inbound later:

- add provider-based inbound webhooks
- or self-host mail on the VPS as a separate ops project

## VPS deploy steps

1. SSH into the VPS.
2. Run `sudo bash deploy/setup-vps.sh` from a fresh clone, or follow its steps manually.
3. Edit `/opt/arclane/repo/.env`.
4. Run `cd /opt/arclane/repo && docker compose up -d --build`.
5. Run `cd /opt/arclane/repo && docker compose exec arclane alembic upgrade head`.
6. Confirm:
   - `https://arclane.cloud/health`
   - `https://arclane.cloud/dashboard`
   - `https://arclane.cloud/live`

## Smoke test

Run:

```bash
ARCLANE_SMOKE_EMAIL="you@example.com" \
ARCLANE_SMOKE_PASSWORD="your-test-password" \
bash deploy/smoke-test.sh arclane.cloud
```

Then manually verify:

1. OAuth login works for Google and GitHub
2. New venture intake works
3. Existing business intake works
4. First cycle starts
5. `Next Three Outputs` renders
6. Tenant subdomain resolves
7. Add-on offer appears after the triggering output completes
8. Add-on checkout opens through Vinzy / Stripe
9. Vinzy callback queues the purchased add-on

## First-customer release gate

Do not treat launch as complete until all of these are true:

- OAuth works in production
- Postgres is live and backed up
- Wildcard DNS is live
- Caddy has issued TLS for apex and tenant routes
- Vinzy / Stripe checkout works for plans, credit packs, and add-ons
- Outbound mail works
- The smoke test passes
- One real end-to-end business creation flow succeeds live
