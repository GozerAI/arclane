"""Business settings and persisted operating plan."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.config import settings as app_settings
from arclane.core.database import get_session
from arclane.engine.operating_plan import enqueue_add_on
from arclane.models.tables import Activity, Business
from arclane.performance.business_cache import business_config_cache

router = APIRouter()


class AgentTaskEntry(BaseModel):
    key: str
    output_key: str | None = None
    kind: str | None = None
    area: str
    action: str
    title: str | None = None
    status_label: str
    brief: str
    description: str
    expected_output: str
    depends_on: list[str]
    queue_status: str | None = None
    duration_days: int | None = None
    days_remaining: int | None = None
    working_days_required: int | None = None
    supersedes_queue: bool | None = None
    included_cycles_total: int | None = None
    included_cycles_remaining: int | None = None


class CreditModel(BaseModel):
    definition: str
    cadence: str
    acceleration_model: str


class AddOnOffer(BaseModel):
    key: str
    title: str
    detail: str
    trigger_output_key: str
    status: str
    working_days_required: int
    supersedes_queue: bool


class UserRecommendation(BaseModel):
    title: str
    detail: str
    task: str


class ProvisioningStep(BaseModel):
    key: str
    label: str
    status: str
    detail: str


class ProvisioningPlan(BaseModel):
    subdomain: str
    mailbox: str
    public_url: str
    workspace_path: str
    steps: list[ProvisioningStep]


class CodeStoragePlan(BaseModel):
    mode: str
    workspace_path: str
    manifest_path: str
    template: str
    strategy: str


class OperatingPlan(BaseModel):
    program_type: str | None = None
    working_day_model: CreditModel | None = None
    intake_brief: dict
    agent_tasks: list[AgentTaskEntry]
    add_on_offers: list[AddOnOffer] = []
    user_recommendations: list[UserRecommendation]
    provisioning: ProvisioningPlan
    code_storage: CodeStoragePlan


class BusinessSettings(BaseModel):
    name: str
    description: str
    website_url: str | None
    website_summary: str | None
    contact_email: str
    slug: str
    subdomain: str
    plan: str
    working_days_remaining: int
    subdomain_provisioned: bool
    email_provisioned: bool
    app_deployed: bool
    template: str | None
    operating_plan: OperatingPlan | None = None


class BusinessUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


def _serialize_settings(business: Business) -> BusinessSettings:
    return BusinessSettings(
        name=business.name,
        description=business.description,
        website_url=business.website_url,
        website_summary=business.website_summary,
        contact_email=f"{business.slug}@{app_settings.email_from_domain}",
        slug=business.slug,
        subdomain=f"{business.slug}.{app_settings.domain}",
        plan=business.plan,
        working_days_remaining=business.working_days_remaining + business.working_days_bonus,
        subdomain_provisioned=business.subdomain_provisioned,
        email_provisioned=business.email_provisioned,
        app_deployed=business.app_deployed,
        template=business.template,
        operating_plan=(business.agent_config or {}).get("operating_plan"),
    )


@router.get("", response_model=BusinessSettings)
async def get_settings(
    business: Business = Depends(get_business),
):
    return _serialize_settings(business)


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
    business_config_cache.invalidate(business.slug)
    return _serialize_settings(business)


@router.post("/add-ons/{add_on_key}", response_model=BusinessSettings)
async def queue_add_on(
    add_on_key: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    operating_plan = (business.agent_config or {}).get("operating_plan") or {}
    offers = operating_plan.get("add_on_offers") or []
    offer = next((item for item in offers if item.get("key") == add_on_key), None)
    if not offer:
        raise HTTPException(status_code=404, detail="Add-on not found")
    if offer.get("status") != "available":
        raise HTTPException(status_code=400, detail="This add-on is not available yet")

    updated_agent_config = dict(business.agent_config or {})
    updated_agent_config["operating_plan"] = enqueue_add_on(operating_plan, add_on_key)
    business.agent_config = updated_agent_config

    session.add(
        Activity(
            business_id=business.id,
            agent="system",
            action="Add-on queued",
            detail=(
                f"{offer.get('title', 'Add-on')} moved ahead of the normal queue and includes "
                f"{offer.get('working_days_required', 1)} dedicated night"
                f"{'' if offer.get('working_days_required', 1) == 1 else 's'} of execution."
            ),
        )
    )
    await session.commit()
    await session.refresh(business)
    return _serialize_settings(business)
