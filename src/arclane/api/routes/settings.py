"""Business settings."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.core.config import settings as app_settings
from arclane.models.tables import Business

router = APIRouter()


class BusinessSettings(BaseModel):
    name: str
    description: str
    slug: str
    subdomain: str
    plan: str
    credits_remaining: int
    subdomain_provisioned: bool
    email_provisioned: bool
    app_deployed: bool
    template: str | None


class BusinessUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("", response_model=BusinessSettings)
async def get_settings(
    business: Business = Depends(get_business),
):
    return BusinessSettings(
        name=business.name,
        description=business.description,
        slug=business.slug,
        subdomain=f"{business.slug}.{app_settings.domain}",
        plan=business.plan,
        credits_remaining=business.credits_remaining + business.credits_bonus,
        subdomain_provisioned=business.subdomain_provisioned,
        email_provisioned=business.email_provisioned,
        app_deployed=business.app_deployed,
        template=business.template,
    )


@router.patch("", response_model=BusinessSettings)
async def update_settings(
    payload: BusinessUpdate,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    if payload.name is not None:
        business.name = payload.name
    if payload.description is not None:
        business.description = payload.description
    await session.commit()
    await session.refresh(business)
    return BusinessSettings(
        name=business.name,
        description=business.description,
        slug=business.slug,
        subdomain=f"{business.slug}.{app_settings.domain}",
        plan=business.plan,
        credits_remaining=business.credits_remaining + business.credits_bonus,
        subdomain_provisioned=business.subdomain_provisioned,
        email_provisioned=business.email_provisioned,
        app_deployed=business.app_deployed,
        template=business.template,
    )
