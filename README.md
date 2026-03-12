# Arclane

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

AI-powered website builder and deployment platform. Create a business, generate content, and deploy sites — all managed through automated cycles powered by AI agents.

Part of the [GozerAI](https://gozerai.com) ecosystem.

## Features

- **One-command business creation** with automatic site generation
- **AI-driven cycles** that build, optimize, and deploy content
- **Live SSE feed** for real-time progress monitoring
- **Template system** with content-site, SaaS app, and landing page templates
- **Docker-based deployment** with automatic port allocation and health monitoring
- **JWT authentication** with business ownership verification

## Feature Tiers

| Feature | Community | Pro | Enterprise |
|---------|:---------:|:---:|:----------:|
| API, CLI, core services | x | x | x |
| Business & cycle management | x | x | x |
| Models & database | x | x | x |
| Site generation engine | | x | x |
| Third-party integrations | | x | x |
| Tenant provisioning | | | x |
| Email notifications | | | x |

## Quick Start

```bash
git clone https://github.com/GozerAI/arclane.git
cd arclane
pip install -e ".[dev]"

# Configure environment
export ARCLANE_SECRET_KEY="your-secret-key"
export ARCLANE_DATABASE_URL="sqlite+aiosqlite:///arclane.db"

# Run database migrations
alembic upgrade head

# Start the server
uvicorn arclane.api.app:app --host 0.0.0.0 --port 8012
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ARCLANE_SECRET_KEY` | — | JWT signing key |
| `ARCLANE_DATABASE_URL` | `sqlite+aiosqlite:///arclane.db` | Database connection string |
| `ARCLANE_ENV` | `development` | `development` or `production` |
| `ARCLANE_DOMAIN` | `arclane.cloud` | Base domain for deployed sites |
| `ARCLANE_CADDY_ADMIN_URL` | `http://localhost:2019` | Caddy reverse proxy admin API |
| `ARCLANE_ENGINE_BASE_URL` | `http://localhost:8007` | Site generation engine URL |
| `ARCLANE_SENTRY_DSN` | — | Sentry error tracking DSN |

## API Reference

Authentication: `Authorization: Bearer <jwt_token>` (required in production, optional in development).

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register a new account |
| POST | `/api/v1/auth/login` | Login and receive JWT token |
| POST | `/api/v1/auth/forgot-password` | Request password reset |
| POST | `/api/v1/auth/reset-password` | Reset password with token |

### Businesses

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/intake` | Create a new business |
| GET | `/api/v1/businesses/:slug` | Get business details |
| PUT | `/api/v1/businesses/:slug/settings` | Update business settings |

### Cycles

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/businesses/:slug/cycles` | Trigger a new cycle |
| GET | `/api/v1/businesses/:slug/cycles` | List cycles for a business |
| GET | `/api/v1/businesses/:slug/metrics` | Business metrics |

### Content & Feed

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/businesses/:slug/content` | Get generated content |
| GET | `/live/:slug` | SSE live feed for a business |
| GET | `/api/v1/feed` | Global activity feed |

### Billing

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/billing/plans` | Available plans |
| POST | `/api/v1/billing/subscribe` | Subscribe to a plan |

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Basic health check |
| GET | `/health/detailed` | Detailed health (DB, upstream services) |

## Docker Deployment

```bash
docker compose up -d
```

The Dockerfile and `docker-compose.yml` handle the full stack including Caddy reverse proxy and automatic HTTPS.

Deploy scripts are available in `deploy/` for VPS setup and redeployment.

## Templates

| Template | Description |
|----------|-------------|
| `content-site` | Express + blog with content management |
| `saas-app` | Express + authentication scaffold |
| `landing-page` | Static marketing page |

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details. Commercial licenses available at [gozerai.com](https://gozerai.com).
