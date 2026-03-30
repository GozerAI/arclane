"""Business intake — create and list businesses."""

import re
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.app import limiter
from arclane.api.auth import get_current_user_email
from arclane.billing.policy import company_limit_for_account
from arclane.core.database import async_session, get_session
from arclane.core.config import settings
from arclane.core.logging import get_logger
from arclane.engine.operating_plan import build_operating_plan
from arclane.engine.website_intelligence import (
    compose_business_context,
    fetch_website_snapshot,
    summarize_website,
)
from arclane.models.schemas import BusinessCreate, BusinessResponse, VALID_TEMPLATES
from arclane.models.tables import Activity, Business, Cycle
from arclane.notifications import send_mailbox_ready_email, send_welcome_email
from arclane.provisioning.service import provision_business

log = get_logger("intake")
router = APIRouter()

RESERVED_SLUGS = {
    "admin", "api", "mail", "www", "ftp", "dns", "ns", "mx",
    "smtp", "imap", "pop", "support", "status", "health",
    "dashboard", "live", "feed", "billing", "auth", "login",
    "signup", "register", "static", "assets", "cdn", "media",
    "blog", "app", "help", "docs", "dev", "staging", "test",
}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:63]


def _clean_generated_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\s-]+", "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:255]


def _extract_title_from_summary(summary: str | None) -> str | None:
    if not summary:
        return None
    match = re.search(r"Title:\s*([^\n.;|]+)", summary, flags=re.IGNORECASE)
    if not match:
        return None
    title = _clean_generated_name(match.group(1))
    return title or None


def _website_name_candidate(website_url: str, website_summary: str | None) -> str:
    title = _extract_title_from_summary(website_summary)
    if title:
        return title

    hostname = urlparse(website_url).hostname or "business"
    root = hostname.replace("www.", "").split(".")[0]
    words = [part.capitalize() for part in re.split(r"[-_]+", root) if part]
    candidate = " ".join(words) or "Website Launch"
    return _clean_generated_name(candidate)


def _idea_name_candidate(description: str) -> str:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]+", description.lower())
    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "your", "their", "into", "have",
        "will", "want", "what", "does", "help", "helps", "build", "business", "company",
        "service", "platform", "tool", "startup", "agency", "system", "product", "users",
        "people", "teams", "operators", "planning", "existing", "trying", "accomplish",
    }
    keywords = [token for token in tokens if token not in stopwords and len(token) > 2]
    if not keywords:
        return "New Venture"

    primary = keywords[0].capitalize()
    suffix_rules = [
        ({"automation", "workflow", "agent", "agents"}, "Flow"),
        ({"booking", "schedule", "scheduling", "appointments"}, "Book"),
        ({"finance", "financial", "accounting", "revenue"}, "Ledger"),
        ({"analytics", "data", "insights", "research"}, "Scope"),
        ({"security", "compliance", "risk"}, "Shield"),
        ({"content", "marketing", "media", "social"}, "Signal"),
        ({"health", "fitness", "wellness", "medical"}, "Pulse"),
        ({"commerce", "shop", "retail", "store"}, "Cart"),
    ]

    suffix = "Works"
    token_set = set(keywords)
    for matches, label in suffix_rules:
        if token_set & matches:
            suffix = label
            break

    if primary.lower().endswith(suffix.lower()):
        return primary
    return _clean_generated_name(f"{primary} {suffix}")


async def _reserve_name_and_slug(
    session: AsyncSession,
    desired_name: str | None,
    description: str,
    website_url: str | None,
    website_summary: str | None,
) -> tuple[str, str]:
    base_name = _clean_generated_name(desired_name or "")
    if not base_name:
        base_name = (
            _website_name_candidate(website_url, website_summary)
            if website_url
            else _idea_name_candidate(description)
        )

    slug = _slugify(base_name) or "new-venture"
    if slug in RESERVED_SLUGS:
        raise HTTPException(status_code=400, detail=f"The name '{base_name}' is reserved")

    suffix = 1
    candidate_name = base_name
    candidate_slug = slug
    while True:
        existing = await session.execute(select(Business.id).where(Business.slug == candidate_slug))
        if not existing.scalar_one_or_none():
            return candidate_name, candidate_slug
        suffix += 1
        candidate_name = _clean_generated_name(f"{base_name} {suffix}")
        candidate_slug = _slugify(candidate_name)


def _require_auth(request: Request) -> str:
    """Require authentication and return the user's email."""
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    return email


async def _provision_business_background(business_id: int) -> None:
    """Run provisioning outside the request path and record visible progress."""
    async with async_session() as session:
        business = await session.get(Business, business_id)
        if not business:
            log.error("Provisioning skipped: business %d not found", business_id)
            return

        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Provisioning started",
                detail="Setting up domain, business address, and deployment in the background.",
            )
        )
        await session.commit()

        await provision_business(business, session=session)

        completed_steps = []
        mailbox = f"{business.slug}@{settings.email_from_domain}"
        if business.subdomain_provisioned:
            completed_steps.append("subdomain live")
        if business.email_provisioned:
            completed_steps.append(f"business address configured at {mailbox}")
        if business.app_deployed or not business.template:
            completed_steps.append("workspace deployed")

        session.add(
            Activity(
                business_id=business.id,
                agent="system",
                action="Provisioning complete" if completed_steps else "Provisioning update",
                detail=", ".join(completed_steps) if completed_steps else "Background setup is still pending.",
            )
        )
        await session.commit()
        if business.email_provisioned:
            try:
                await send_mailbox_ready_email(business.name, business.owner_email, business.slug, mailbox)
            except Exception:
                log.exception("Failed to send business-address notification for %s", business.slug)


@router.post("", response_model=BusinessResponse, status_code=201)
@limiter.limit("5/minute")
async def create_business(
    request: Request,
    payload: BusinessCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user_email: str = Depends(_require_auth),
):
    description = (payload.description or "").strip()
    website_url = str(payload.website_url) if payload.website_url else None
    selected_template = payload.template or "content-site"

    if selected_template not in VALID_TEMPLATES:
        raise HTTPException(status_code=400, detail="Invalid template")

    if not description and not website_url:
        raise HTTPException(status_code=400, detail="Provide a business description or website URL")

    owned_result = await session.execute(
        select(Business.plan)
        .where(Business.owner_email == current_user_email)
        .where(~Business.slug.startswith("_user-"))
    )
    owned_plans = [row[0] for row in owned_result.all()]
    company_limit = company_limit_for_account(owned_plans)
    if len(owned_plans) >= company_limit:
        raise HTTPException(
            status_code=402,
            detail=f"Plan limit reached. Your account supports {company_limit} company"
            f"{'' if company_limit == 1 else 'ies'}. Upgrade to add more.",
        )

    website_summary = None
    if website_url:
        website_summary = summarize_website(await fetch_website_snapshot(website_url))
    business_context = compose_business_context(description, website_summary, website_url)
    business_name, slug = await _reserve_name_and_slug(
        session,
        payload.name,
        business_context or description,
        website_url,
        website_summary,
    )

    # Use the authenticated user's email, not the request body
    business = Business(
        slug=slug,
        name=business_name,
        description=business_context or description or f"Optimize the existing business at {website_url}.",
        website_url=website_url,
        website_summary=website_summary,
        owner_email=current_user_email,
        template=selected_template,
        agent_config={
            "operating_plan": build_operating_plan(
                name=business_name,
                slug=slug,
                description=business_context or description or f"Optimize the existing business at {website_url}.",
                template=selected_template,
                website_url=website_url,
                website_summary=website_summary,
            )
        },
    )
    session.add(business)
    await session.commit()
    await session.refresh(business)

    log.info("Business created: %s (%s)", business.name, business.slug)

    # Initialize 90-day roadmap
    from arclane.services.roadmap_service import initialize_roadmap
    await initialize_roadmap(business, session)
    await session.commit()
    await session.refresh(business)

    # Kick off provisioning (non-blocking — failures logged, not raised)
    # Trigger initial cycle in background (uses first-month bonus working day)
    cycle = Cycle(
        business_id=business.id,
        trigger="initial",
        status="pending",
    )
    session.add(cycle)
    if business.working_days_bonus > 0:
        business.working_days_bonus -= 1
    else:
        business.working_days_remaining -= 1
    await session.commit()
    await session.refresh(cycle)
    session.add(
        Activity(
            business_id=business.id,
            cycle_id=cycle.id,
            agent="system",
            action="Business launched",
            detail="Initial setup has started. Your first autonomous cycle is queued.",
        )
    )
    operating_plan = (business.agent_config or {}).get("operating_plan") or {}
    agent_task_count = len(operating_plan.get("agent_tasks") or [])
    provisioning_steps = len((operating_plan.get("provisioning") or {}).get("steps") or [])
    session.add(
        Activity(
            business_id=business.id,
            cycle_id=cycle.id,
            agent="system",
            action="Operating plan prepared",
            detail=f"{agent_task_count} agent tasks mapped, {provisioning_steps} provisioning steps queued.",
        )
    )
    if website_url:
        session.add(
            Activity(
                business_id=business.id,
                cycle_id=cycle.id,
                agent="system",
                action="Website analyzed",
                detail=website_summary[:300] if website_summary else f"Captured {website_url} for optimization.",
            )
        )
    await session.commit()

    from arclane.api.routes.cycles import _run_cycle
    background_tasks.add_task(_provision_business_background, business.id)
    background_tasks.add_task(_run_cycle, business.id, cycle.id)
    log.info("Initial cycle %d queued for %s", cycle.id, business.slug)

    background_tasks.add_task(send_welcome_email, business.name, business.owner_email, business.slug)

    resp = BusinessResponse(
        id=business.id,
        slug=business.slug,
        name=business.name,
        description=business.description,
        website_url=business.website_url,
        plan=business.plan,
        subdomain=f"{business.slug}.{settings.domain}",
        working_days_remaining=business.working_days_remaining + business.working_days_bonus,
        created_at=business.created_at,
    )
    return resp


@router.get("", response_model=list[BusinessResponse])
async def list_businesses(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user_email: str = Depends(_require_auth),
):
    # Only return real businesses owned by the authenticated user (exclude auth-only stubs)
    query = (
        select(Business)
        .where(Business.owner_email == current_user_email)
        .where(~Business.slug.startswith("_user-"))
        .order_by(Business.created_at.desc())
    )
    result = await session.execute(query)
    businesses = result.scalars().all()
    return [
        BusinessResponse(
            id=b.id,
            slug=b.slug,
            name=b.name,
            description=b.description,
            website_url=b.website_url,
            plan=b.plan,
            subdomain=f"{b.slug}.{settings.domain}",
            working_days_remaining=b.working_days_remaining + b.working_days_bonus,
            created_at=b.created_at,
        )
        for b in businesses
    ]
