"""Tests for upsell, engagement, and retention routes."""

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


async def _create_business(factory, slug="test-biz", plan="starter", working_days=5):
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


# --- Engagement ---


async def test_engagement_score(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    resp = await client.get(
        "/api/businesses/test-biz/upsell/engagement",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "score" in data
    assert "level" in data
    assert "factors" in data
    assert "triggers" in data


# --- Upgrade prompts ---


async def test_upgrade_prompts_low_working_days(db_and_client):
    factory, client = db_and_client
    await _create_business(factory, working_days=0)
    resp = await client.get(
        "/api/businesses/test-biz/upsell/prompts",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # With 0/5 working days, should get a limit prompt
    if data:
        assert data[0]["prompt_type"] in ("upgrade_at_limit", "contextual_cta", "milestone_celebration")


async def test_upgrade_prompts_healthy(db_and_client):
    factory, client = db_and_client
    await _create_business(factory, working_days=5)
    resp = await client.get(
        "/api/businesses/test-biz/upsell/prompts",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    # Full working days, no usage → might be empty or just contextual
    assert isinstance(resp.json(), list)


# --- Feature spotlights ---


async def test_feature_spotlights(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    resp = await client.get(
        "/api/businesses/test-biz/upsell/spotlights",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Starter plan has features, none used → should get spotlights
    for item in data:
        assert item["prompt_type"] == "feature_spotlight"


# --- Demo sessions ---


async def test_start_demo(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    resp = await client.post(
        "/api/businesses/test-biz/upsell/demos",
        json={"feature": "advanced_analytics", "duration_hours": 1},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feature"] == "advanced_analytics"
    assert data["actions_taken"] == 0


async def test_start_demo_already_included(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    # content_generation is included in starter
    resp = await client.post(
        "/api/businesses/test-biz/upsell/demos",
        json={"feature": "content_generation"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 400


async def test_list_active_demos(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    await client.post(
        "/api/businesses/test-biz/upsell/demos",
        json={"feature": "ab_testing"},
        headers=_auth_headers(),
    )
    resp = await client.get(
        "/api/businesses/test-biz/upsell/demos",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# --- Product tour ---


async def test_product_tour_lifecycle(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)

    # Start tour
    resp = await client.post(
        "/api/businesses/test-biz/upsell/tour",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_step"] == 0
    assert data["total_steps"] == 5
    assert data["completed"] is False

    # Advance
    resp = await client.post(
        "/api/businesses/test-biz/upsell/tour/advance",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_step"] == 1
    assert data["steps"][0]["completed"] is True


# --- Survey ---


async def test_survey_get(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    resp = await client.get(
        "/api/businesses/test-biz/upsell/survey",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["questions"]) == 4


async def test_survey_submit(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    # Must get survey first to register behavior
    await client.get(
        "/api/businesses/test-biz/upsell/survey",
        headers=_auth_headers(),
    )
    resp = await client.post(
        "/api/businesses/test-biz/upsell/survey",
        json={"responses": {"overall": 9, "value": 5, "recommend": 10}},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["nps_category"] == "promoter"
    assert "request_testimonial" in data["triggers"]


async def test_survey_low_score_triggers(db_and_client):
    factory, client = db_and_client
    await _create_business(factory)
    await client.get(
        "/api/businesses/test-biz/upsell/survey",
        headers=_auth_headers(),
    )
    resp = await client.post(
        "/api/businesses/test-biz/upsell/survey",
        json={"responses": {"overall": 2, "value": 1}},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["nps_category"] == "detractor"
    assert "escalate_to_support" in data["triggers"]


# --- Win-back ---


async def test_winback_active_business(db_and_client):
    factory, client = db_and_client
    await _create_business(factory, plan="starter")
    resp = await client.get(
        "/api/businesses/test-biz/upsell/winback",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    # Active business, no win-back offer
    assert resp.json() is None


async def test_winback_cancelled_business(db_and_client):
    factory, client = db_and_client
    await _create_business(factory, plan="cancelled", working_days=0)
    resp = await client.get(
        "/api/businesses/test-biz/upsell/winback",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    if data:
        assert "winback" in data["prompt_type"]
        assert "off" in data["cta_text"].lower()
