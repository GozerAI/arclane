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
    credits_remaining: int
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


# --- Billing ---


class WebhookEvent(str, Enum):
    SUBSCRIPTION_CREATED = "subscription.created"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
    SUBSCRIPTION_RENEWED = "subscription.renewed"
    CREDITS_PURCHASED = "credits.purchased"
    ADD_ON_PURCHASED = "add_on.purchased"
