# Arclane

Autonomous business engine. Users describe a business, Arclane builds and runs it.

## Architecture

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy async, APScheduler
- **Engine**: Wraps C-Suite (F:\Projects\c-suite) — 16 executives hidden behind simple interface
- **Bridge**: POST /api/v1/arclane/cycle on C-Suite, batch task execution
- **Provisioning**: Subdomain (Caddy), email (Resend), deploy (Docker per-tenant)
- **Auth**: Delegates to Zuultimate (F:\Projects\zuultimate)
- **Billing**: Stripe via Vinzy, 3 plans (starter $49, pro $99, enterprise $199)
- **Frontend**: Vanilla HTML/CSS/JS — landing page + dashboard SPA + live feed

## Project Layout

```
src/arclane/
  api/          → FastAPI app, routes (feed, content, metrics, settings, intake, auth, billing, cycles, live)
  core/         → Config (ARCLANE_ prefix), logging, database
  engine/       → Orchestrator (C-Suite bridge), scheduler, intake pipeline
  provisioning/ → Subdomain, email, templates, deploy
  models/       → SQLAlchemy tables (Business, Cycle, Activity, Content, Metric) + Pydantic schemas
frontend/
  landing.html  → Marketing page at /
  index.html    → Dashboard SPA at /dashboard
  live.html     → Public live feed at /live
  static/       → app.js, style.css
templates/      → content-site, saas-app, landing-page
deploy/         → VPS setup + redeploy scripts
```

## Key Patterns

- C-Suite agents never exposed — user sees "Creating content" not "CMO executing"
- Initial cycle auto-triggers on business creation (uses bonus credit)
- Nightly scheduler runs autonomous cycles at ARCLANE_NIGHTLY_HOUR
- Config uses ARCLANE_ env prefix (pydantic-settings)
- Port 8012 in Docker (standalone and GozerAI infra)

## Routes

- `/` → marketing landing page
- `/dashboard` → SPA (login/create/dashboard)
- `/live` → public live feed with SSE
- `/api/businesses` → CRUD
- `/api/businesses/{slug}/feed` → activity feed
- `/api/businesses/{slug}/content` → content items
- `/api/businesses/{slug}/cycles` → trigger/list cycles
- `/api/businesses/{slug}/billing` → status/checkout/webhook
- `/api/businesses/{slug}/metrics` → time-series metrics
- `/api/businesses/{slug}/settings` → business settings
- `/api/live` → public feed + stats + SSE stream
- `/api/auth` → login/validate (Zuultimate proxy)
- `/health` → health check

## Running

```bash
pip install -e ".[dev]"
uvicorn arclane.api.app:app --port 8012
```

## Testing

```bash
pytest tests/ -q   # 37 tests, ~41s
```

## Related Projects

- C-Suite: F:\Projects\c-suite (16-executive agent engine, bridge endpoint)
- Zuultimate: F:\Projects\zuultimate (identity/auth)
- Vinzy: F:\Projects\vinzy-engine (licensing)
- GozerAI Infra: F:\Projects\gozerai-infra (docker orchestration)
- Trendscope: F:\Projects\trendscope (market intelligence)
