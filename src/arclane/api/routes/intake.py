"""Business intake — create and list businesses."""

import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.app import limiter
from arclane.core.database import get_session
from arclane.core.config import settings
from arclane.core.logging import get_logger
from arclane.models.schemas import BusinessCreate, BusinessResponse, VALID_TEMPLATES
from arclane.models.tables import Business, Cycle
from arclane.notifications import send_welcome_email
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


@router.post("", response_model=BusinessResponse, status_code=201)
@limiter.limit("5/minute")
async def create_business(
    request: Request,
    payload: BusinessCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    slug = _slugify(payload.name)

    if slug in RESERVED_SLUGS:
        raise HTTPException(status_code=400, detail="This name is reserved")

    if payload.template and payload.template not in VALID_TEMPLATES:
        raise HTTPException(status_code=400, detail="Invalid template")

    existing = await session.execute(select(Business).where(Business.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A business with this name already exists")

    business = Business(
        slug=slug,
        name=payload.name,
        description=payload.description,
        owner_email=payload.owner_email,
        template=payload.template,
    )
    session.add(business)
    await session.commit()
    await session.refresh(business)

    log.info("Business created: %s (%s)", business.name, business.slug)

    # Kick off provisioning (non-blocking — failures logged, not raised)
    try:
        await provision_business(business, session=session)
    except Exception:
        log.exception("Provisioning failed for %s (non-fatal)", business.slug)

    # Trigger initial cycle in background (uses first-month bonus credit)
    cycle = Cycle(
        business_id=business.id,
        trigger="initial",
        status="pending",
    )
    session.add(cycle)
    if business.credits_bonus > 0:
        business.credits_bonus -= 1
    else:
        business.credits_remaining -= 1
    await session.commit()
    await session.refresh(cycle)

    from arclane.api.routes.cycles import _run_cycle
    background_tasks.add_task(_run_cycle, business.id, cycle.id)
    log.info("Initial cycle %d queued for %s", cycle.id, business.slug)

    background_tasks.add_task(send_welcome_email, business.name, business.owner_email, business.slug)

    resp = BusinessResponse(
        id=business.id,
        slug=business.slug,
        name=business.name,
        description=business.description,
        plan=business.plan,
        subdomain=f"{business.slug}.{settings.domain}",
        credits_remaining=business.credits_remaining + business.credits_bonus,
        created_at=business.created_at,
    )
    return resp


@router.get("", response_model=list[BusinessResponse])
async def list_businesses(
    owner_email: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    if not owner_email:
        raise HTTPException(status_code=400, detail="owner_email parameter required")
    query = select(Business).where(Business.owner_email == owner_email).order_by(Business.created_at.desc())
    result = await session.execute(query)
    businesses = result.scalars().all()
    return [
        BusinessResponse(
            id=b.id,
            slug=b.slug,
            name=b.name,
            description=b.description,
            plan=b.plan,
            subdomain=f"{b.slug}.{settings.domain}",
            credits_remaining=b.credits_remaining + b.credits_bonus,
            created_at=b.created_at,
        )
        for b in businesses
    ]
