"""Pydantic schemas for API request/response."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


# --- Intake ---

VALID_TEMPLATES = {"content-site", "saas-app", "landing-page"}


class BusinessCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=10000)
    owner_email: EmailStr
    template: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]*$", max_length=50)


class BusinessResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: str
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

    model_config = {"from_attributes": True}


# --- Billing ---


class WebhookEvent(str, Enum):
    SUBSCRIPTION_CREATED = "subscription.created"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
    SUBSCRIPTION_RENEWED = "subscription.renewed"
