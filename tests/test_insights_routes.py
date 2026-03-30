"""Tests for insights routes — LTV, onboarding funnel, A/B testing, content scheduling."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base, Business, Content, Cycle


@pytest.fixture
async def db_and_client():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield factory, client
    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_business(factory, slug="test-biz", plan="pro", working_days=20):
    async with factory() as session:
        biz = Business(
            slug=slug, name="Test Biz", description="Test",
            owner_email="test@test.com", plan=plan,
            working_days_remaining=working_days, working_days_bonus=0,
        )
        session.add(biz)
        await session.commit()
        return biz.id


def _auth_headers():
    import jwt
    from arclane.core.config import settings
    token = jwt.encode(
        {"sub": "test@test.com", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.secret_key, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# --- LTV prediction ---


async def test_ltv_prediction(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    resp = await client.post(
        "/api/businesses/test-biz/insights/ltv",
        json={"monthly_revenue_cents": 9900, "engagement_score": 70.0},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["predicted_ltv_cents"] > 0
    assert 0 <= data["confidence"] <= 1
    assert data["predicted_months_remaining"] > 0
    assert "retention_rate" in data["factors"]


async def test_ltv_prediction_low_engagement(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    resp = await client.post(
        "/api/businesses/test-biz/insights/ltv",
        json={"monthly_revenue_cents": 4900, "engagement_score": 10.0, "churn_signals": 3},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["churn_probability"] > 0.1  # High churn with low engagement


# --- Onboarding funnel ---


async def test_onboarding_funnel(db_and_client):
    factory, client = db_and_client
    biz_id = await _create_business(factory)
    async with factory() as session:
        session.add(Cycle(
            business_id=biz_id, trigger="initial", status="completed",
        ))
        session.add(Content(
            business_id=biz_id, content_type="blog", title="First",
            body="Content", status="draft",
        ))
        await session.commit()

    resp = await client.get("/api/insights/onboarding-funnel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_signups"] >= 1
    assert data["first_cycle"] >= 1
    assert data["first_content"] >= 1
    assert "stage_rates" in data


# --- A/B testing ---


async def test_experiment_lifecycle(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)

    # Create
    resp = await client.post(
        "/api/insights/experiments",
        json={
            "name": "Pricing Test",
            "description": "Test two pricing pages",
            "variants": [
                {"name": "control", "description": "Current pricing"},
                {"name": "treatment", "description": "New pricing"},
            ],
        },
    )
    assert resp.status_code == 200
    exp = resp.json()
    exp_id = exp["id"]
    assert exp["status"] == "draft"
    assert len(exp["variants"]) == 2

    # Start
    resp = await client.post(f"/api/insights/experiments/{exp_id}/start")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    # Assign
    resp = await client.post(
        f"/api/businesses/test-biz/insights/experiments/{exp_id}/assign",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    variant = resp.json()["variant"]
    assert variant in ("control", "treatment")

    # Convert
    resp = await client.post(
        f"/api/businesses/test-biz/insights/experiments/{exp_id}/convert",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["converted"] is True

    # Results
    resp = await client.get(f"/api/insights/experiments/{exp_id}/results")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_participants"] == 1
    assert variant in data["variant_stats"]

    # Complete
    resp = await client.post(f"/api/insights/experiments/{exp_id}/complete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


async def test_experiment_needs_two_variants(db_and_client):
    _, client = db_and_client
    resp = await client.post(
        "/api/insights/experiments",
        json={
            "name": "Bad Test",
            "description": "Only one variant",
            "variants": [{"name": "only_one"}],
        },
    )
    assert resp.status_code == 400


# --- Journey analytics ---


async def test_journey_analytics_empty(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/insights/journeys")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_journeys"] == 0


# --- Content scheduling ---


async def test_content_schedule_with_published_at(db_and_client):
    factory, client = db_and_client
    biz_id = await _create_business(factory)
    async with factory() as session:
        session.add(Content(
            business_id=biz_id, content_type="blog", title="Scheduled",
            body="Future content", status="draft",
        ))
        await session.commit()

    future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    resp = await client.patch(
        "/api/businesses/test-biz/content/1",
        json={"status": "scheduled", "published_at": future},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "scheduled"
    assert data["published_at"] is not None


async def test_content_schedule_requires_published_at(db_and_client):
    factory, client = db_and_client
    biz_id = await _create_business(factory)
    async with factory() as session:
        session.add(Content(
            business_id=biz_id, content_type="blog", title="No Time",
            body="Content", status="draft",
        ))
        await session.commit()

    resp = await client.patch(
        "/api/businesses/test-biz/content/1",
        json={"status": "scheduled"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 400
    assert "published_at" in resp.json()["detail"]


async def test_content_publish_immediately(db_and_client):
    factory, client = db_and_client
    biz_id = await _create_business(factory)
    async with factory() as session:
        session.add(Content(
            business_id=biz_id, content_type="social", title="Now",
            body="Content", status="draft",
        ))
        await session.commit()

    resp = await client.patch(
        "/api/businesses/test-biz/content/1",
        json={"status": "published"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"
    assert resp.json()["published_at"] is not None
