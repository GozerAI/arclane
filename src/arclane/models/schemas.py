"""Pydantic schemas for API request/response."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, HttpUrl


# --- Intake ---

VALID_TEMPLATES = {"content-site", "saas-app", "landing-page"}


class BusinessCreate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    website_url: HttpUrl | None = None
    owner_email: EmailStr | None = None  # Ignored — JWT email is used by the endpoint
    template: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]*$", max_length=50)


class BusinessResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    website_url: str | None = None
    plan: str
    subdomain: str
    working_days_remaining: int
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Activity Feed ---


class ActivityEntry(BaseModel):
    id: int
    action: str
    detail: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Content ---

VALID_CONTENT_TYPES = {"blog", "social", "newsletter", "changelog", "report"}
VALID_CONTENT_STATUSES = {"draft", "published", "scheduled"}


class ContentEntry(BaseModel):
    id: int
    content_type: str
    title: str | None
    body: str
    platform: str | None
    status: str
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Metrics ---


class MetricEntry(BaseModel):
    id: int
    name: str
    value: float
    recorded_at: datetime

    model_config = {"from_attributes": True}


# --- Cycles ---


class CycleRequest(BaseModel):
    task_description: str | None = Field(None, max_length=5000)


class CycleResponse(BaseModel):
    id: int
    trigger: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_tasks: int | None = None
    failed_tasks: int | None = None

    model_config = {"from_attributes": True}


class ContentUpdateRequest(BaseModel):
    status: str = Field(..., pattern=r"^(draft|published|scheduled)$")
    published_at: datetime | None = None


# --- Billing ---


class WebhookEvent(str, Enum):
    SUBSCRIPTION_CREATED = "subscription.created"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
    SUBSCRIPTION_RENEWED = "subscription.renewed"
    WORKING_DAYS_PURCHASED = "credits.purchased"
    ADD_ON_PURCHASED = "add_on.purchased"


# --- Roadmap ---


class MilestoneResponse(BaseModel):
    key: str
    title: str
    category: str
    status: str
    target_value: float | None = None
    current_value: float | None = None
    due_day: int | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class PhaseResponse(BaseModel):
    phase_number: int
    phase_name: str
    status: str
    graduation_score: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    milestones_total: int = 0
    milestones_completed: int = 0
    milestones: list[MilestoneResponse] = []


class RoadmapResponse(BaseModel):
    roadmap_day: int
    current_phase: int
    graduation_date: datetime | None = None
    total_days: int = 90
    progress_pct: float = 0.0
    phases: list[PhaseResponse] = []


# --- Health ---


class HealthSubScore(BaseModel):
    market_fit: float = 0.0
    content: float = 0.0
    revenue: float = 0.0
    operations: float = 0.0
    momentum: float = 0.0


class HealthScoreResponse(BaseModel):
    overall: float
    sub_scores: dict[str, float] = {}
    factors: dict = {}


class HealthTrendEntry(BaseModel):
    score: float
    factors: dict | None = None
    recorded_at: datetime


# --- Advisory ---


class AdvisoryNoteResponse(BaseModel):
    id: int
    category: str
    title: str
    body: str
    priority: int
    acknowledged: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class WeeklyDigestResponse(BaseModel):
    period: dict
    roadmap_day: int
    current_phase: int
    cycles: dict
    content: dict
    milestones: dict
    revenue: dict
    top_notes: list[dict] = []


# --- Revenue Tracking ---


class RevenueEventResponse(BaseModel):
    id: int
    source: str
    amount_cents: int
    currency: str = "usd"
    event_date: datetime

    model_config = {"from_attributes": True}


class RevenueSummaryResponse(BaseModel):
    total_cents: int
    total_usd: float
    total_events: int
    by_source: dict = {}
    monthly: list[dict] = []


class ROIResponse(BaseModel):
    total_revenue_cents: int
    total_revenue_usd: float
    estimated_cost_cents: int
    estimated_cost_usd: float
    roi_pct: float
    months_active: int
    plan: str


# --- Distribution ---


class DistributionChannelResponse(BaseModel):
    id: int
    platform: str
    status: str
    last_published_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Competitors ---


class CompetitorResponse(BaseModel):
    id: int
    name: str
    url: str | None = None
    findings: dict | None = None
    last_checked_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CompetitiveBriefResponse(BaseModel):
    business: str
    competitors_tracked: int
    competitors: list[dict] = []
    generated_at: datetime


# --- Advertising ---

VALID_AD_PLATFORMS = {"google", "facebook", "instagram", "linkedin", "twitter"}
VALID_CAMPAIGN_TYPES = {"awareness", "traffic", "conversion", "retargeting"}
VALID_CAMPAIGN_STATUSES = {"draft", "review", "active", "paused", "completed"}
VALID_AD_TONES = {"professional", "casual", "urgent", "playful", "empathetic"}


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    platform: str = Field(..., pattern=r"^(google|facebook|instagram|linkedin|twitter)$")
    campaign_type: str = Field("awareness", pattern=r"^(awareness|traffic|conversion|retargeting)$")
    budget_cents: int = Field(0, ge=0)
    target_audience: dict | None = None
    schedule: dict | None = None


class CampaignResponse(BaseModel):
    id: int
    name: str
    platform: str
    campaign_type: str
    status: str
    budget_cents: int
    spent_cents: int
    target_audience: dict | None = None
    performance: dict | None = None
    launched_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdCopyGenerate(BaseModel):
    campaign_type: str = Field("awareness", pattern=r"^(awareness|traffic|conversion|retargeting)$")
    tone: str = Field("professional", pattern=r"^(professional|casual|urgent|playful|empathetic)$")
    num_variations: int = Field(3, ge=1, le=10)
    platform: str = Field("facebook", pattern=r"^(google|facebook|instagram|linkedin|twitter)$")
    key_message: str | None = Field(None, max_length=500)


class AdCopyResponse(BaseModel):
    id: int
    headline: str
    body: str
    cta: str | None = None
    image_prompt: str | None = None
    platform_format: str
    tone: str
    status: str
    performance: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CustomerSegmentResponse(BaseModel):
    id: int
    name: str
    description: str
    demographics: dict | None = None
    psychographics: dict | None = None
    behaviors: dict | None = None
    estimated_size: str | None = None
    priority: int
    platform_targeting: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CampaignLaunchResponse(BaseModel):
    campaign_id: int
    status: str
    platform: str
    ad_copies_count: int
    segments_applied: int
    message: str
