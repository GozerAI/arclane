"""Subdomain routing — serves business content on {slug}.arclane.cloud.

When a request arrives on a subdomain (e.g. beanbridge.arclane.cloud),
this middleware intercepts it and serves the business's generated content
instead of the main Arclane marketing site.
"""

import json
import re
import markdown
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select

from arclane.api.page_renderer import render_landing_page
from arclane.core.config import settings
from arclane.core.database import async_session
from arclane.core.logging import get_logger
from arclane.models.tables import Activity, Business, Content, Metric

log = get_logger("subdomain")

# Match {slug}.arclane.cloud but not bare arclane.cloud
_SUBDOMAIN_RE = re.compile(
    rf"^([a-z0-9][a-z0-9-]*)\.{re.escape(settings.domain)}$", re.IGNORECASE
)

# Internal slugs that should not be treated as business subdomains
_RESERVED = {"www", "api", "app", "mail", "contact", "status", "docs"}


def _extract_slug(host: str) -> str | None:
    """Return the business slug from a subdomain host, or None."""
    m = _SUBDOMAIN_RE.match(host.split(":")[0])  # strip port if present
    if not m:
        return None
    slug = m.group(1).lower()
    if slug in _RESERVED or slug.startswith("_"):
        return None
    return slug


def _render_page(business_name: str, slug: str, items: list[Content]) -> str:
    """Render a business portal page from generated content."""
    cards = []
    for item in items:
        body_html = markdown.markdown(
            item.body or "",
            extensions=["tables", "fenced_code"],
        )
        type_label = (item.content_type or "report").replace("_", " ").title()
        cards.append(f"""
        <div class="card">
            <div class="card-header">
                <span class="card-type">{type_label}</span>
            </div>
            <div class="card-title">{item.title or "Untitled"}</div>
            <div class="card-body">{body_html}</div>
        </div>""")

    cards_html = "\n".join(cards) if cards else '<p class="empty">No content yet — check back soon.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{business_name} — Powered by Arclane</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #cbd5e1; line-height: 1.7; }}
.container {{ max-width: 800px; margin: 0 auto; padding: 2rem 1.5rem; }}
.page-header {{ text-align: center; margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid rgba(148,163,184,0.1); }}
.page-header h1 {{ font-size: 1.5rem; color: #e2e8f0; margin-bottom: 0.3rem; }}
.page-header p {{ font-size: 0.9rem; color: #64748b; }}
.card {{ background: rgba(15,23,42,0.6); border: 1px solid rgba(148,163,184,0.08); border-radius: 12px; margin-bottom: 2rem; overflow: hidden; }}
.card-header {{ display: flex; justify-content: space-between; padding: 0.75rem 1.5rem; background: rgba(30,41,59,0.5); border-bottom: 1px solid rgba(148,163,184,0.06); }}
.card-type {{ font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: #818cf8; }}
.card-title {{ font-size: 1.15rem; font-weight: 600; color: #e2e8f0; padding: 1.25rem 1.5rem 0.5rem; }}
.card-body {{ padding: 0 1.5rem 1.5rem; font-size: 0.9rem; }}
.card-body h1 {{ font-size: 1.3rem; color: #e2e8f0; margin: 1.5rem 0 0.5rem; }}
.card-body h2 {{ font-size: 1.15rem; color: #e2e8f0; margin: 1.5rem 0 0.5rem; padding-bottom: 0.3rem; border-bottom: 1px solid rgba(148,163,184,0.1); }}
.card-body h3 {{ font-size: 1rem; color: #e2e8f0; margin: 1.2rem 0 0.4rem; }}
.card-body strong {{ color: #e2e8f0; }}
.card-body ul {{ padding-left: 1.2rem; margin: 0.5rem 0; }}
.card-body li {{ margin-bottom: 0.3rem; }}
.card-body blockquote {{ border-left: 3px solid #4f46e5; padding: 0.5rem 1rem; margin: 0.75rem 0; background: rgba(79,70,229,0.06); color: #a5b4fc; font-style: italic; }}
.card-body hr {{ border: none; border-top: 1px solid rgba(148,163,184,0.1); margin: 1.2rem 0; }}
.card-body table {{ width: 100%; border-collapse: collapse; margin: 0.75rem 0; font-size: 0.85rem; }}
.card-body th {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 2px solid rgba(148,163,184,0.15); color: #94a3b8; font-weight: 600; }}
.card-body td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid rgba(148,163,184,0.06); }}
.empty {{ text-align: center; color: #64748b; padding: 3rem 0; }}
.footer {{ text-align: center; padding: 2rem 0; font-size: 0.8rem; color: #475569; border-top: 1px solid rgba(148,163,184,0.1); margin-top: 2rem; }}
.footer a {{ color: #818cf8; text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
    <div class="page-header">
        <h1>{business_name}</h1>
        <p>Business intelligence powered by Arclane</p>
    </div>
    {cards_html}
    <div class="footer">
        Powered by <a href="https://arclane.cloud">Arclane</a>
    </div>
</div>
</body>
</html>"""


class SubdomainMiddleware(BaseHTTPMiddleware):
    """Intercept subdomain requests and serve business content."""

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        slug = _extract_slug(host)

        if not slug:
            return await call_next(request)

        # Handle lead capture POST on subdomains
        if request.url.path == "/signup" and request.method == "POST":
            return await self._handle_signup(slug, request)

        # Handle checkout POST on subdomains (Stripe Connect)
        if request.url.path == "/checkout" and request.method == "POST":
            return await self._handle_checkout(slug, request)

        # Only intercept the root page — let API/static requests through
        if request.url.path not in ("/", ""):
            return await call_next(request)

        async with async_session() as session:
            result = await session.execute(
                select(Business).where(Business.slug == slug).limit(1)
            )
            business = result.scalar_one_or_none()

            if not business:
                return await call_next(request)

            # Fetch all content for this business
            content_result = await session.execute(
                select(Content)
                .where(Content.business_id == business.id, Content.body.isnot(None))
                .order_by(Content.created_at.asc())
            )
            items = list(content_result.scalars().all())

        # Try structured landing page first (JSON with design tokens)
        landing = next(
            (c for c in items if c.content_type == "blog" and c.body),
            None,
        )
        if landing:
            has_stripe = bool(business.stripe_connect_id and business.stripe_connect_onboarded)
            rendered = render_landing_page(business.name, landing.body, has_stripe=has_stripe)
            if rendered:
                return HTMLResponse(content=rendered)

        # Fallback: card-based layout for legacy/markdown content
        html = _render_page(business.name, slug, items)
        return HTMLResponse(content=html)

    async def _handle_signup(self, slug: str, request: Request) -> JSONResponse:
        """Capture a lead from the subdomain signup form."""
        try:
            body = await request.body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        email = (data.get("email") or "").strip()
        name = (data.get("name") or "").strip()

        if not email or "@" not in email:
            return JSONResponse({"error": "Valid email required"}, status_code=400)

        async with async_session() as session:
            result = await session.execute(
                select(Business).where(Business.slug == slug).limit(1)
            )
            business = result.scalar_one_or_none()

            if not business:
                return JSONResponse({"error": "Not found"}, status_code=404)

            session.add(Metric(
                business_id=business.id,
                name="lead_captured",
                value=1.0,
                metadata_json={
                    "source": "landing_page",
                    "email": email,
                    "name": name,
                },
            ))
            session.add(Activity(
                business_id=business.id,
                agent="system",
                action="Lead captured",
                detail=f"New signup from landing page: {name or email}",
            ))
            await session.commit()

        log.info("Lead captured for %s: %s", slug, email)
        return JSONResponse({"ok": True, "message": "Thanks for signing up!"})

    async def _handle_checkout(self, slug: str, request: Request) -> JSONResponse:
        """Create a Stripe Checkout session for a subdomain purchase."""
        from arclane.services.stripe_connect import create_checkout_session

        try:
            body = await request.body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        plan_name = data.get("plan", "")
        amount_cents = data.get("amount_cents", 0)

        if not plan_name or not amount_cents or amount_cents < 100:
            return JSONResponse({"error": "Plan and amount required (min $1)"}, status_code=400)

        async with async_session() as session:
            result = await session.execute(
                select(Business).where(Business.slug == slug).limit(1)
            )
            business = result.scalar_one_or_none()

            if not business:
                return JSONResponse({"error": "Not found"}, status_code=404)

            if not business.stripe_connect_id:
                return JSONResponse({"error": "Payments not set up for this business"}, status_code=400)

        host = request.headers.get("host", slug + ".arclane.cloud")
        base = f"https://{host}"

        try:
            checkout = await create_checkout_session(
                connected_account_id=business.stripe_connect_id,
                product_name=f"{business.name} — {plan_name}",
                amount_cents=amount_cents,
                success_url=f"{base}/?checkout=success",
                cancel_url=f"{base}/?checkout=cancelled",
                transaction_type=data.get("type", "sale"),
                metadata={"business_slug": slug, "plan": plan_name},
            )
        except Exception:
            log.warning("Checkout session creation failed for %s", slug, exc_info=True)
            return JSONResponse({"error": "Payment system unavailable"}, status_code=502)

        return JSONResponse({"ok": True, "checkout_url": checkout["url"]})
