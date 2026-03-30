"""Stripe Connect routes — onboarding and dashboard access for business owners."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business, get_session
from arclane.core.config import settings
from arclane.core.logging import get_logger
from arclane.models.tables import Business
from arclane.services.stripe_connect import (
    create_connect_account,
    create_login_link,
    create_onboarding_link,
    get_account_status,
)

log = get_logger("routes.connect")
router = APIRouter()


class ConnectStatusResponse(BaseModel):
    connected: bool
    charges_enabled: bool = False
    payouts_enabled: bool = False
    onboarded: bool = False
    dashboard_url: str | None = None


class OnboardingResponse(BaseModel):
    url: str


@router.get("/connect/status", response_model=ConnectStatusResponse)
async def connect_status(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Check Stripe Connect status for this business."""
    if not business.stripe_connect_id:
        return ConnectStatusResponse(connected=False)

    try:
        status = await get_account_status(business.stripe_connect_id)
    except Exception:
        log.warning("Failed to check Connect status for %s", business.slug)
        return ConnectStatusResponse(connected=True)

    dashboard_url = None
    if status["onboarded"]:
        try:
            dashboard_url = await create_login_link(business.stripe_connect_id)
        except Exception:
            pass

    return ConnectStatusResponse(
        connected=True,
        charges_enabled=status["charges_enabled"],
        payouts_enabled=status["payouts_enabled"],
        onboarded=status["onboarded"],
        dashboard_url=dashboard_url,
    )


@router.post("/connect/onboard", response_model=OnboardingResponse)
async def start_onboarding(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Start or resume Stripe Connect onboarding.

    Creates a Connect Express account if one doesn't exist,
    then returns the Stripe-hosted onboarding URL.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    base = f"https://{settings.domain}" if settings.env == "production" else "http://localhost:8012"

    if not business.stripe_connect_id:
        account_id = await create_connect_account(business.slug, business.owner_email)
        business.stripe_connect_id = account_id
        await session.commit()
    else:
        account_id = business.stripe_connect_id

    url = await create_onboarding_link(
        account_id=account_id,
        return_url=f"{base}/dashboard?connect=complete",
        refresh_url=f"{base}/dashboard?connect=refresh",
    )

    return OnboardingResponse(url=url)


@router.get("/connect/dashboard")
async def connect_dashboard(
    business: Business = Depends(get_business),
):
    """Get a link to the business owner's Stripe Express dashboard."""
    if not business.stripe_connect_id:
        raise HTTPException(status_code=404, detail="Stripe not connected")

    try:
        url = await create_login_link(business.stripe_connect_id)
    except Exception:
        raise HTTPException(status_code=502, detail="Could not generate dashboard link")

    return {"url": url}
