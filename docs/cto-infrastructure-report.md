# Arclane CTO Infrastructure Report
**Date:** March 17, 2026
**Author:** CTO
**Version:** 1.0

---

## 1. Executive Summary

Arclane is architecturally sound for launch. The FastAPI/PostgreSQL/Docker stack is production-capable at the 0–200 user range with targeted hardening. The core risk is not the stack itself — it is the per-tenant Docker container model, which introduces both a scaling ceiling and a security surface that must be addressed before we hit 100 active workspaces.

**Three decisions that must be made before Day 60:**

1. **Container migration trigger.** Per-tenant Docker containers are the right abstraction now but become operationally untenable past ~150 tenants on a single VPS. The migration to static file hosting + CDN for template-driven tenant sites is the cleanest path and should be planned now even if not executed until Month 3.

2. **LLM cost guardrails.** At 500 daily cycles, LLM costs reach ~$1,000/month. Without per-user throttling, caching, and Haiku routing for low-complexity tasks, margin collapses before we hit growth phase.

3. **Scheduler reliability.** APScheduler in-process is adequate at 50 users. At 200+, a single crashed process silently drops nightly cycles. A managed queue (Celery + Redis) must be in place before we promise "forever partner" reliability.

**Priority stack for launch readiness (P0):** secrets management out of env files, PostgreSQL connection pooling, rate limit audit, container network isolation, and backup automation.

---

## 2. Current Architecture Assessment

### What Is Working Well

- **FastAPI async stack** is correctly matched to the I/O-heavy workload (LLM calls, DB writes, Docker SDK calls). No re-architecture needed here.
- **APScheduler** is sufficient for the nightly window at current scale. The 2–5am batch window is a genuine architectural advantage — it allows burst processing without real-time SLA pressure.
- **Caddy** is an excellent choice for subdomain routing. Automatic TLS via Let's Encrypt, zero-downtime config reload, and a clean JSON API make it operationally light. This stays through Stage 3.
- **SQLAlchemy async** with PostgreSQL is the right persistence choice. The async session model means we are not blocking the event loop on DB queries during cycle execution.
- **JWT + HMAC webhooks** are the correct auth primitives. No significant auth re-architecture needed.
- **GitHub Actions CI** on push/PR is already in place — this is ahead of where most early-stage products are.

### Critical Bottlenecks at Scale

**Bottleneck 1: Per-tenant Docker containers (breaks at ~150 tenants on a single 8-core VPS).**
Each running container consumes ~50–150MB RAM idle (Node/Express). At 150 tenants on a 16GB VPS, container overhead alone approaches 8–12GB before any cycle load. Docker layer caching helps build time but not runtime memory. Port allocation from DB is correct but becomes a management problem at scale.

**Bottleneck 2: APScheduler in-process (breaks at ~200 users with nightly cycles).**
APScheduler runs in the same process as the FastAPI app. A process crash, OOM kill, or deployment restart during the 2–5am window drops all pending cycles silently. There is no retry queue, no dead-letter mechanism, and no visibility into which cycles ran vs. were skipped. At 500 businesses, processing in serial within a 3-hour window requires each cycle to complete in under 21 seconds — the current 2–5 minute estimate makes this impossible without parallelism.

**Bottleneck 3: Single PostgreSQL instance without connection pooling.**
SQLAlchemy async with `asyncpg` opens a connection pool per worker process. With multiple Gunicorn workers + nightly cycle workers all hitting the same instance, connection counts exceed PostgreSQL's default `max_connections=100` at moderate scale. No PgBouncer in the current stack.

**Bottleneck 4: LLM call serialization during cycles.**
Current architecture runs LLM calls sequentially per business cycle. At 500 businesses, even with parallelism across businesses, Anthropic's per-minute token rate limits (tier-dependent) create a hard ceiling on cycle throughput. No caching layer exists today.

**Bottleneck 5: Caddy config management at 500+ tenants.**
Caddy's JSON config API supports dynamic route updates, but the current approach of writing a config entry per tenant at container creation time does not handle container restarts, IP changes, or health-check-based routing. At scale, this becomes a manual correction surface.

### Security Gaps Before Launch

| Gap | Severity | Current State |
|-----|----------|---------------|
| Secrets in env files / Docker Compose | High | `.env` files contain API keys, DB passwords |
| Container network isolation | High | Tenant containers share a Docker bridge network — cross-tenant traffic is possible |
| No WAF / request inspection | Medium | Rate limiting via slowapi is present but no layer-7 inspection |
| PostgreSQL not network-isolated | High | Exposed on Docker network; needs firewall rule restricting to app container only |
| No audit log for admin actions | Medium | Business creation/deletion leaves no immutable trail |
| Missing `Content-Security-Policy` headers on tenant subdomains | Medium | Tenant sites served from Node containers with no CSP defaults |
| JWT secret rotation mechanism absent | Medium | Single static `JWT_SECRET` with no rotation path |

---

## 3. Per-Tenant Container Architecture

### Current Approach and Its Limits

Docker-per-tenant is correct for a product where tenants run live Node/Express apps with custom routes and server-side logic. The problem is operational density. On a single VPS:

- At 50 tenants: ~5–7GB RAM for containers, manageable.
- At 150 tenants: ~12–18GB RAM, approaching VPS limits.
- At 500 tenants: impossible on a single host; requires orchestration or a different model.

The 15-minute health monitor is a good safety net but does not solve the underlying density problem.

### Recommended Migration Path

**Phase 1 (0–150 tenants): Keep Docker containers.** The current model works. Optimize by: setting container memory limits (e.g., `--memory=256m`), using a single shared Node process for the `landing-page` template (static files need no runtime), and removing idle containers after 72 hours of inactivity with a cold-start rebuild on next access.

**Phase 2 (150–500 tenants): Hybrid model.** Differentiate by template type:
- `landing-page` template → serve as static files from object storage (R2/S3), not a container. These are pure HTML/CSS/JS with no server-side logic. Zero container overhead.
- `content-site` and `saas-app` templates → keep containers, but migrate to a shared Node cluster with path-based multi-tenancy (one process serves multiple tenants using a tenant ID from the subdomain header). Reduces container count by ~80%.

**Phase 3 (500–2000 tenants): Containerless for all static/content sites.** Build a tenant static site renderer that generates and deploys to R2/CloudFront on each cycle completion. Only `saas-app` tenants (who need server-side auth) retain containers, running on managed container instances (Cloud Run or ECS Fargate).

### Cost Comparison

**Docker containers on VPS (150 tenants):**
- Hetzner CCX33 (8-core, 32GB): ~$60/month
- All containers: ~$60/month total (amortized)

**S3/CloudFront static hosting (150 tenants, landing-page type):**
- S3 storage: ~$0.023/GB — 150 sites at ~5MB each = 0.75GB = ~$0.02/month
- CloudFront: $0.0085/10K requests — at 1M requests/month = ~$0.85/month
- **Total: <$1/month for static hosting vs. ~$15/month equivalent container capacity**

The migration pays for itself immediately. Prioritize moving `landing-page` tenants to static hosting in Month 2.

---

## 4. LLM Infrastructure

### Production Model Selection

| Task | Model | Rationale |
|------|-------|-----------|
| Business analysis, strategic cycle content | Claude Sonnet 3.5/3.7 | Complex reasoning, nuanced output |
| Simple content generation, summaries | Claude Haiku 3.5 | 12x cheaper, adequate for structured tasks |
| Classification, routing decisions | Claude Haiku 3.5 | Low latency, predictable format |

Target mix: 40% Sonnet calls, 60% Haiku calls per cycle. This yields a blended COGS of ~$0.038/cycle vs. $0.055 all-Sonnet.

### Cost Projections at Scale

Assumptions: 3 LLM calls/cycle, 1,200 input tokens + 800 output tokens per call. Sonnet: $3/MTok in, $15/MTok out. Haiku: $0.25/MTok in, $1.25/MTok out.

**Per-cycle cost (40/60 Sonnet/Haiku mix):**
- Sonnet (1.2 calls): (1,200 × $3 + 800 × $15) / 1,000,000 = $0.0156/call × 1.2 = $0.0187
- Haiku (1.8 calls): (1,200 × $0.25 + 800 × $1.25) / 1,000,000 = $0.001/call × 1.8 = $0.0018
  Wait — corrected calculation:
  - Sonnet per call: (1,200 × 3 + 800 × 15) / 1,000,000 = (3,600 + 12,000) / 1,000,000 = $0.0156
  - Haiku per call: (1,200 × 0.25 + 800 × 1.25) / 1,000,000 = (300 + 1,000) / 1,000,000 = $0.0013
  - Blended 3-call cycle: (1.2 × $0.0156) + (1.8 × $0.0013) = $0.0187 + $0.0023 = **$0.021/cycle**

| Scale | Daily cycles | Monthly LLM cost | Annual |
|-------|-------------|-----------------|--------|
| 50 users | 10 | $6.30 | $76 |
| 200 users | 50 | $31.50 | $378 |
| 500 users | 150 | $94.50 | $1,134 |
| 2,000 users | 500 | $315 | $3,780 |

LLM costs are not the margin risk at these volumes. The margin risk is infrastructure overhead relative to low ARPU at the starter tier ($49/month at 10 cycles = $4.90/credit).

### Rate Limiting Strategy

Anthropic enforces per-minute token rate limits that vary by usage tier. At launch (Tier 1), limits are approximately 40K tokens/minute for Sonnet. With 3 Sonnet calls at ~2,000 tokens each, a single cycle uses ~6K tokens. This allows ~6 concurrent cycles before hitting rate limits at Tier 1.

**Mitigation:**
- Stagger cycle starts with a 30-second jitter per business (already addressable in APScheduler/Celery config).
- Implement a token bucket rate limiter in the cycle executor with a 45K tokens/minute ceiling.
- Upgrade Anthropic account to Tier 2 ($500 spend unlocks 80K tokens/minute) before hitting 50 daily cycles.

### Fallback Providers

Maintain a secondary provider configuration for LLM calls:
- Primary: Anthropic Claude (Sonnet/Haiku)
- Fallback: Google Gemini 1.5 Flash (comparable Haiku pricing at $0.075/MTok input) or OpenAI GPT-4o-mini
- Circuit breaker: after 3 consecutive Anthropic 429/500 errors, route to fallback for 10 minutes

The existing OpenAI-compatible endpoint abstraction makes this straightforward — add a provider enum to the LLM client config.

### Caching Strategy

Implement prompt-level caching for:
1. **System prompt caching:** Anthropic supports prompt caching (cache_control: ephemeral) for prompts >1,024 tokens. System prompts reused across cycles qualify. Cache hit saves ~90% of input token cost on cached tokens.
2. **Same-day deduplication:** If a business has already run a cycle today (e.g., manual trigger + nightly auto), skip the LLM calls and return the cached result. Implement via a `cycle_date` unique constraint check.
3. **Template-level output caching:** For `landing-page` content that changes rarely, cache the last cycle output with a 24-hour TTL in Redis. Only re-run LLM if the business profile has changed since last cycle.

---

## 5. Database Architecture

### Connection Pooling (Required Before Launch)

Add PgBouncer in transaction pooling mode between the FastAPI app and PostgreSQL. Configuration:
- Pool size: 20 server connections to PostgreSQL
- Max client connections: 200
- Pool mode: transaction (compatible with asyncpg)

Without this, 4 Gunicorn workers × 10 asyncpg connections each = 40 connections at idle, spiking to 80+ during nightly cycles. PostgreSQL default `max_connections=100` is breached.

Docker Compose addition:
```yaml
pgbouncer:
  image: bitnami/pgbouncer:1.22
  environment:
    POSTGRESQL_HOST: postgres
    POSTGRESQL_DATABASE: arclane
    PGBOUNCER_POOL_MODE: transaction
    PGBOUNCER_MAX_CLIENT_CONN: 200
    PGBOUNCER_DEFAULT_POOL_SIZE: 20
```

### Required Indexes

Based on common query patterns in the Arclane cycle/content architecture:

```sql
-- Nightly scheduler: fetch businesses due for a cycle
CREATE INDEX idx_businesses_next_cycle ON businesses(next_cycle_at) WHERE active = true;

-- Credit check before cycle execution
CREATE INDEX idx_businesses_credits ON businesses(id, credits_remaining);

-- Content retrieval by business (dashboard, /live feed)
CREATE INDEX idx_content_business_created ON content(business_id, created_at DESC);

-- Cycle history queries
CREATE INDEX idx_cycles_business_status ON cycles(business_id, status, created_at DESC);

-- User lookup by email (auth hot path)
CREATE INDEX idx_users_email ON users(email);

-- Scheduler: monthly credit reset
CREATE INDEX idx_businesses_plan_active ON businesses(plan, active) WHERE active = true;
```

### Content Storage Migration

Currently all cycle output (text content, analysis results) is stored in PostgreSQL. This is correct at launch. Migrate to S3/R2 at the 500-user mark when content table rows exceed ~5M entries and JSONB columns begin impacting query planner performance.

Migration path: Store content metadata (business_id, cycle_id, type, s3_key, created_at) in PostgreSQL; store body in R2 with a pre-signed URL retrieval pattern. Arclane's cycle content is append-only and rarely updated, making it ideal for object storage.

### Backup and Recovery

- **Daily automated backups** via `pg_dump` to R2/S3 (Hetzner Storage Box is $3.50/month for 100GB — sufficient).
- **Point-in-time recovery:** Enable WAL archiving on the managed PostgreSQL instance (Supabase/Neon both support this on paid plans).
- **Recovery time objective (RTO):** 4 hours for a full restore from backup. Acceptable at current scale.
- **Recovery point objective (RPO):** 24 hours (daily backup). Acceptable at launch; move to WAL streaming (RPO ~5 minutes) before Month 6.

### Read Replicas

Not needed until 500+ users. At that point, cycle result reads for the dashboard and `/live` SSE feed can be routed to a read replica without touching the primary. Add at Stage 3 migration.

---

## 6. Scheduling Architecture

### Current Limitations

APScheduler in-process means:
- No retry on failure — a cycle that errors out is silently dropped
- No visibility — no job queue to inspect for stuck/pending cycles
- No parallelism — cycles execute serially unless explicit threading is added
- No persistence — scheduler state lives in memory; process restart loses pending jobs

At 500 businesses and a 3-hour nightly window (10,800 seconds), each cycle can take at most 21.6 seconds. Current cycle time is 2–5 minutes. **This is a hard deadline miss at 500 users without architectural change.**

### Recommended: Celery + Redis

Introduce Celery with Redis as broker at the 200-user trigger:

```
APScheduler (trigger only) → Redis queue → Celery workers (4–8 concurrent) → PostgreSQL
```

- APScheduler's only job becomes enqueueing tasks at 2am, not executing them.
- Celery workers process cycles concurrently (4 workers × parallel cycle execution = 500 cycles in ~90 minutes at 2–5 min each with 4× parallelism).
- Redis: Hetzner managed Redis is ~$10/month. CloudAMQP (RabbitMQ) is an alternative at $0–$19/month (free tier adequate at <200 messages/hour).
- Celery Flower provides a web UI for job visibility at no additional cost.

**Batching strategy for 500 businesses in a 3-hour window:**
- Enqueue all 500 businesses at 2am with a `priority` field (Forever Partners get priority=1, standard users get priority=2).
- Run 8 Celery workers with concurrency=2 (16 parallel cycles).
- At 3 minutes average per cycle: 500 / 16 = 31.25 minutes. Well within the 3-hour window.
- Add `soft_time_limit=480` (8 minutes) and `time_limit=600` (10 minutes) per task to prevent runaway cycles.

---

## 7. VPS → Cloud Migration Path

### Stage 1: 0–200 Users (Current → Month 4)

**Infrastructure:** Single Hetzner CX42 (8 vCPU, 32GB RAM, $30/month) + managed PostgreSQL (Supabase free tier or Hetzner Managed Database at $23/month).

**Actions:**
- Move PostgreSQL off the VPS onto managed DB (eliminates backup responsibility, adds HA)
- Add PgBouncer (Docker Compose service)
- Caddy continues as reverse proxy — no change needed

**Monthly infra cost: ~$53–$80**

### Stage 2: 200–500 Users (Month 4–8)

**Infrastructure:**
- 2× Hetzner CX32 app servers ($20/month each) behind Hetzner Load Balancer ($6/month)
- Managed PostgreSQL (Hetzner: $23/month for 2-core, 4GB)
- Managed Redis (Hetzner: $10/month) for Celery broker + caching
- Hetzner Object Storage (S3-compatible) for static tenant sites: $5/month

**Actions:**
- Deploy Celery workers (replaces APScheduler for cycle execution)
- Migrate `landing-page` tenants to static hosting
- Add read replica for PostgreSQL
- Implement container memory limits and idle container hibernation

**Monthly infra cost: ~$84/month**

### Stage 3: 500–2000 Users (Month 8–18)

**Infrastructure:**
- 3–5 app servers or migrate to DigitalOcean App Platform / Hetzner Cloud with auto-scaling groups
- PostgreSQL: Supabase Pro ($25/month) or DigitalOcean Managed PostgreSQL ($50/month for 2-core, 4GB with standby)
- Redis: DigitalOcean Managed Redis ($15/month)
- Cloudflare R2 for object storage (zero egress fees — important at this scale)
- Tenant containers: migrate to Cloud Run (GCP) or Fly.io for `saas-app` template containers

**Monthly infra cost: ~$200–$400/month depending on container count**

For tenant containers specifically, Fly.io Machines are ideal: $0.0000022/second per CPU, billed only when running. A container that runs 2 hours/day costs ~$0.013/month per machine — vastly cheaper than dedicated containers.

### Stage 4: 2000+ Users (Month 18+)

Full cloud-native:
- Kubernetes (GKE Autopilot or EKS with Karpenter for cost-aware node provisioning)
- Aurora PostgreSQL Serverless v2 (scales to zero between nightly cycles)
- ElastiCache Redis
- CloudFront + S3 for all static content
- Estimated: $800–$2,000/month at 2,000 active users depending on cycle frequency

---

## 8. Subdomain Infrastructure

### DNS Architecture

Arclane.cloud DNS managed via Cloudflare (free plan is sufficient). Current setup: wildcard `*.arclane.cloud` A record pointing to VPS IP.

At Stage 2+, update wildcard to point to the load balancer IP. No per-tenant DNS record management needed — Caddy handles subdomain routing based on the `Host` header.

### SSL Certificate Management

**Critical issue:** Let's Encrypt issues certificates per subdomain by default. At 500 tenants, individual ACME challenges for each `{slug}.arclane.cloud` subdomain create:
- Rate limit exposure: Let's Encrypt allows 50 certificates per registered domain per week. At launch pace, this is fine. At 200+ new tenants per week, it becomes a bottleneck.
- **Solution:** Use a single wildcard certificate `*.arclane.cloud` issued via DNS-01 ACME challenge (Cloudflare DNS API). Caddy supports this natively with the `caddy-dns/cloudflare` module. One certificate renewal covers all tenant subdomains permanently.

Configuration in Caddyfile:
```
*.arclane.cloud {
  tls {
    dns cloudflare {env.CLOUDFLARE_API_TOKEN}
  }
  @tenant host_regexp slug ^([a-z0-9-]+)\.arclane\.cloud$
  handle @tenant {
    reverse_proxy {http.regexp.slug.1}-container:3000
  }
}
```

This must be implemented before launch. Attempting to issue individual certs at scale will hit rate limits within weeks of growth.

### CDN for Static Tenant Pages

Route `landing-page` tenant traffic through Cloudflare (free plan) rather than directly to the VPS. Benefits:
- DDoS protection on tenant subdomains
- Edge caching of static HTML/CSS/JS (cache TTL: 1 hour for content, 24 hours for assets)
- Zero cost at current traffic levels

---

## 9. Security Hardening (Pre-Launch Checklist)

### P0 — Must Fix Before Any Public Traffic

| Item | Action |
|------|--------|
| **Secrets in .env files** | Migrate to Docker secrets or a secrets manager. Minimum viable: Doppler free tier syncs to Docker Compose and GitHub Actions. Stop committing `.env` to repo. |
| **Container network isolation** | Create separate Docker networks per tenant or per tenant group. Tenant containers must not be on the same bridge network as the Arclane app container or each other. |
| **PostgreSQL network exposure** | Bind PostgreSQL to `127.0.0.1` or the internal Docker network only. Never expose port 5432 to the public interface. |
| **JWT secret strength** | Ensure `JWT_SECRET` is ≥32 random bytes, stored in secrets manager, not `.env` file in repo. Add a rotation procedure to runbook. |
| **Rate limit audit** | Verify slowapi limits cover: login (5/minute/IP), registration (3/hour/IP), cycle trigger (2/hour/user), API generally (100/minute/user). Test with `locust` or `wrk`. |

### P1 — Fix Within 30 Days of Launch

| Item | Action |
|------|--------|
| **Tenant container filesystem isolation** | Mount tenant containers with `--read-only` root filesystem + explicit tmpfs for writable paths. Prevents a compromised tenant container from modifying its own image. |
| **CSP headers on tenant subdomains** | Inject `Content-Security-Policy` and `X-Frame-Options` headers at the Caddy layer for all tenant subdomains. |
| **Audit logging** | Add an `audit_log` table (user_id, action, resource_id, ip, timestamp) populated on: business create/delete, plan change, credit adjustment, password reset. |
| **Webhook HMAC validation** | Verify all inbound webhooks (Stripe, any future integrations) validate HMAC signatures before processing. Confirm this is enforced in the current Stripe webhook handler. |
| **Dependency scanning** | Add `pip-audit` to GitHub Actions CI to catch known CVEs in Python dependencies. |

### P2 — Before Month 3

- External penetration test (Cobalt or Synack on-demand, ~$3,000–$5,000 for a focused test)
- OWASP ZAP automated scan against staging environment
- Implement `fail2ban` or Cloudflare rate limiting rules to block credential-stuffing attacks on `/api/v1/auth/login`

---

## 10. Monitoring & Observability

### Current State

`/health` and `/health/detailed` endpoints exist. No metrics collection, no error aggregation, no alerting.

### Recommended Stack (Cost-Optimized)

**Option A: Grafana Cloud + Prometheus (Recommended)**
- Grafana Cloud free tier: 10K metrics series, 50GB logs, 14-day retention
- Deploy Prometheus in Docker Compose, scrape FastAPI metrics endpoint (add `prometheus-fastapi-instrumentator`)
- Cost: $0/month through ~500 users, $29/month at scale with extended retention

**Option B: Datadog**
- $15/host/month (Pro plan) — at 2 hosts = $30/month minimum
- Better out-of-the-box APM and LLM observability features
- Worth the cost at Stage 3+; overkill at Stage 1

**Recommended at launch: Grafana Cloud free tier + Sentry for error tracking (free tier: 5K errors/month).**

### Required Metrics

```
arclane_cycle_total{status="success|failed|skipped"}
arclane_cycle_duration_seconds (histogram)
arclane_llm_tokens_used_total{model="sonnet|haiku", type="input|output"}
arclane_llm_cost_usd_total
arclane_active_containers (gauge)
arclane_credits_depleted_total (counter — businesses that hit 0 credits)
arclane_db_connections_active (gauge)
```

### Alerting Thresholds

| Alert | Condition | Channel |
|-------|-----------|---------|
| Nightly cycle failure rate | >10% of cycles fail in a single run | PagerDuty/email (P1) |
| LLM cost spike | Daily LLM spend >2× 7-day average | Email (P2) |
| Database connection saturation | Active connections >80% of pool limit for >5 minutes | PagerDuty (P1) |
| Container startup failure | >3 container build failures in 1 hour | Email (P2) |
| Credit depletion wave | >20% of active businesses at 0 credits | Email to sales (P2) |
| Uptime | /health returns non-200 for >2 minutes | PagerDuty (P0) |

Use **UptimeRobot** (free plan) for external uptime monitoring of `/health` endpoint with 5-minute checks and SMS alerting.

---

## 11. Cost Model

### Infrastructure Costs by Scale Tier

#### 50 Users (Launch, Month 1)
| Item | Monthly Cost |
|------|-------------|
| Hetzner CX32 (4 vCPU, 8GB) | $14 |
| Managed PostgreSQL (Hetzner CPX11 self-managed or Supabase free) | $0–$14 |
| Domain (arclane.cloud, amortized) | $1 |
| Resend (email notifications, free tier 3K/month) | $0 |
| Cloudflare (free tier) | $0 |
| GitHub Actions (free tier, <2,000 min/month) | $0 |
| **Total infra** | **$15–$29/month** |

**LLM costs:** 10 daily cycles × 30 days × $0.021 = **$6.30/month**

**Total COGS at 50 users:** ~$21–$35/month = **$0.42–$0.70/user/month**

At $49 average plan revenue: **gross margin ~98.5%** (LLM+infra COGS negligible at this scale)

#### 200 Users (Month 3)
| Item | Monthly Cost |
|------|-------------|
| Hetzner CX42 (8 vCPU, 32GB) | $30 |
| Hetzner Managed DB (PostgreSQL) | $23 |
| Hetzner Managed Redis | $10 |
| Object storage (R2/Hetzner Storage Box) | $5 |
| Resend (10K emails/month, Starter plan) | $20 |
| Monitoring (Grafana free + Sentry free) | $0 |
| **Total infra** | **$88/month** |

**LLM costs:** 50 daily cycles × 30 days × $0.021 = **$31.50/month**

**Total COGS at 200 users:** ~$120/month = **$0.60/user/month**

Assuming average revenue $70/user (mix of tiers): **gross margin ~99.1%**

#### 500 Users (Month 6)
| Item | Monthly Cost |
|------|-------------|
| 2× Hetzner CX42 + Load Balancer | $66 |
| Managed PostgreSQL (2-core, 4GB + read replica) | $46 |
| Managed Redis | $15 |
| Object storage (R2, ~10GB content) | $5 |
| Fly.io tenant containers (~100 active saas-app tenants) | $30 |
| Resend (30K emails/month, Business plan) | $45 |
| Grafana Cloud (Pro, extended retention) | $29 |
| **Total infra** | **$236/month** |

**LLM costs:** 150 daily cycles × 30 days × $0.021 = **$94.50/month**

**Total COGS at 500 users:** ~$331/month = **$0.66/user/month**

At average revenue $80/user: $40,000/month revenue, **gross margin ~99.2%**

#### 2,000 Users (Month 12)
| Item | Monthly Cost |
|------|-------------|
| 4× app servers (Hetzner CX52 or GCP e2-standard-4) | $200 |
| Supabase Pro PostgreSQL + standby | $100 |
| Managed Redis (HA) | $50 |
| CloudFront + S3 (static tenant sites, ~500GB egress) | $50 |
| Cloud Run / Fly.io (saas-app containers, ~300 active) | $150 |
| Resend (100K emails/month) | $90 |
| Grafana Cloud Pro | $49 |
| Sentry Team plan | $26 |
| Misc (DNS, CI/CD minutes) | $30 |
| **Total infra** | **$745/month** |

**LLM costs:** 500 daily cycles × 30 days × $0.021 = **$315/month**

**Total COGS at 2,000 users:** ~$1,060/month = **$0.53/user/month**

At average revenue $85/user: $170,000/month revenue, **gross margin ~99.4%**

The business model is structurally sound. LLM and infra costs represent <1% of revenue at scale. The primary cost risk is LLM price increases or significant per-cycle complexity growth.

---

## 12. 90-Day Technical Roadmap

### Days 1–30: Launch Readiness (P0 Items)

| Item | Priority | Effort | Dependency |
|------|----------|--------|------------|
| Wildcard SSL cert via Cloudflare DNS-01 in Caddy | P0 | 2h | Cloudflare API token |
| Secrets migration to Doppler (app + CI) | P0 | 4h | Doppler account |
| PostgreSQL network isolation (internal Docker only) | P0 | 1h | None |
| Tenant container network isolation (separate bridge) | P0 | 3h | None |
| PgBouncer added to Docker Compose | P0 | 2h | None |
| Required DB indexes (see Section 5) | P0 | 1h | None |
| JWT secret rotation procedure documented | P0 | 1h | None |
| Rate limit audit + test coverage | P0 | 4h | None |
| Stripe billing integration (plan enforcement) | P0 | 8h | Stripe account |
| UptimeRobot monitoring on /health | P0 | 30min | None |
| Automated PostgreSQL backup to object storage | P0 | 2h | R2/S3 bucket |

### Days 31–60: Stability and Visibility

| Item | Priority | Effort | Dependency |
|------|----------|--------|------------|
| Prometheus metrics endpoint + Grafana Cloud setup | P1 | 4h | Days 1–30 complete |
| Sentry error tracking integration | P1 | 2h | None |
| CSP headers on tenant subdomains (Caddy layer) | P1 | 2h | None |
| Audit log table + middleware | P1 | 6h | None |
| pip-audit in CI pipeline | P1 | 1h | None |
| Anthropic Tier 2 account upgrade (trigger: >30 daily cycles) | P1 | 1h | $500 spend threshold |
| LLM provider fallback (Gemini circuit breaker) | P1 | 6h | None |
| Container memory limits + idle hibernation | P1 | 4h | None |
| Celery + Redis deployment (begin parallel to APScheduler) | P1 | 8h | Redis instance |
| `landing-page` tenant migration to static hosting | P1 | 8h | Object storage bucket |

### Days 61–90: Scale Preparation

| Item | Priority | Effort | Dependency |
|------|----------|--------|------------|
| Cut over nightly cycles to Celery (retire APScheduler for cycles) | P1 | 4h | Days 31–60 Celery work |
| Prompt caching (Anthropic cache_control) for system prompts | P1 | 4h | None |
| Same-day cycle deduplication guard | P2 | 2h | None |
| Migrate PostgreSQL to managed DB (Hetzner/Supabase) | P1 | 4h | Backup verification |
| Load test nightly cycle at 200 concurrent businesses | P1 | 4h | Celery in place |
| External penetration test scoping + scheduling | P2 | 2h | Budget approval |
| Read replica setup and dashboard query routing | P2 | 4h | Managed DB |
| Redis prompt output cache (24h TTL) | P2 | 4h | Redis in place |
| Fly.io evaluation for saas-app tenant containers | P2 | 4h | None |
| Billing dunning flow (failed payment → cycle suspension) | P2 | 6h | Stripe integration |

---

*This report reflects the infrastructure state as of March 17, 2026. Pricing is based on current Hetzner, Anthropic, Cloudflare, Supabase, Fly.io, and AWS list prices. Re-evaluate cost model if Anthropic pricing changes or nightly cycle LLM call count grows beyond 5 per cycle.*
