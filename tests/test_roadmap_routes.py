"""Tests for roadmap, health, revenue, distribution, advisory, and competitor routes."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base


@pytest.fixture
async def auth_client_with_business():
    """Authenticated client with a pre-created business. Yields (client, slug)."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    from arclane.api.routes import cycles as cycles_module
    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Register
            reg = await c.post("/api/auth/register", json={
                "email": "test@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})

            # Create business (triggers initialize_roadmap internally)
            resp = await c.post("/api/businesses", json={
                "description": "Test AI business for route testing",
            })
            assert resp.status_code == 201, resp.text
            slug = resp.json()["slug"]

            yield c, slug

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Roadmap routes
# ---------------------------------------------------------------------------


async def test_get_roadmap(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/roadmap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_phase"] == 1
    assert data["roadmap_day"] == 1
    assert data["total_days"] == 60
    assert len(data["phases"]) == 4
    assert data["phases"][0]["phase_name"] == "Foundation"
    assert data["phases"][0]["status"] == "active"
    assert data["phases"][1]["status"] == "locked"


async def test_get_phase(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/roadmap/phase/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["phase_number"] == 1
    assert data["phase_name"] == "Foundation"
    assert data["milestones_total"] > 0
    assert data["milestones_completed"] == 0

    # Non-existent phase
    resp404 = await c.get(f"/api/businesses/{slug}/roadmap/phase/99")
    assert resp404.status_code == 404


async def test_get_milestones(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/roadmap/milestones")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["milestones"]) == data["total"]
    # All milestones should start as pending
    assert all(m["status"] == "pending" for m in data["milestones"])


async def test_complete_milestone(auth_client_with_business):
    c, slug = auth_client_with_business
    # Complete a known Phase 1 milestone
    resp = await c.post(f"/api/businesses/{slug}/roadmap/milestones/p1-strategy-brief/complete")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["milestone_key"] == "p1-strategy-brief"

    # Verify via milestones listing
    ms_resp = await c.get(f"/api/businesses/{slug}/roadmap/milestones")
    milestones = ms_resp.json()["milestones"]
    brief = next(m for m in milestones if m["key"] == "p1-strategy-brief")
    assert brief["status"] == "completed"
    assert brief["completed_at"] is not None

    # Non-existent milestone
    bad = await c.post(f"/api/businesses/{slug}/roadmap/milestones/nonexistent-key/complete")
    assert bad.status_code == 404


async def test_advance_phase_not_ready(auth_client_with_business):
    c, slug = auth_client_with_business
    # Phase 1 milestones not done yet — should not advance
    resp = await c.post(f"/api/businesses/{slug}/roadmap/advance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_ready"
    assert "detail" in data


async def test_check_graduation(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/roadmap/graduation")
    assert resp.status_code == 200
    data = resp.json()
    assert "ready" in data
    assert "score" in data
    assert "met" in data
    assert "unmet" in data
    # Fresh business — should not be ready
    assert data["ready"] is False
    assert len(data["unmet"]) > 0


async def test_next_actions(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/roadmap/next-actions")
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    assert len(data["actions"]) > 0
    # Each action should have at least an action and detail field
    for action in data["actions"]:
        assert "action" in action
        assert "detail" in action


# ---------------------------------------------------------------------------
# Health dashboard routes
# ---------------------------------------------------------------------------


async def test_get_health_score(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "overall" in data
    assert "sub_scores" in data
    assert "factors" in data
    assert set(data["sub_scores"].keys()) == {"market_fit", "content", "revenue", "operations", "momentum"}
    # All scores should be numeric
    assert isinstance(data["overall"], (int, float))


async def test_get_health_trend(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/health/trend")
    assert resp.status_code == 200
    data = resp.json()
    assert "trend" in data
    # No snapshots recorded yet — trend should be empty
    assert data["trend"] == []


async def test_record_health_snapshot(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.post(f"/api/businesses/{slug}/health/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert isinstance(data["score"], (int, float))

    # Now trend should have one entry
    trend_resp = await c.get(f"/api/businesses/{slug}/health/trend")
    trend = trend_resp.json()["trend"]
    assert len(trend) == 1
    assert trend[0]["score"] == data["score"]


async def test_get_health_recommendations(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/health/recommendations")
    assert resp.status_code == 200
    data = resp.json()
    assert "recommendations" in data
    assert "overall" in data
    # Recommendations should be sorted by score ascending
    scores = [r["score"] for r in data["recommendations"]]
    assert scores == sorted(scores)


# ---------------------------------------------------------------------------
# Revenue tracking routes
# ---------------------------------------------------------------------------


async def test_create_revenue_event(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.post(f"/api/businesses/{slug}/revenue-tracking/events", json={
        "source": "stripe",
        "amount_cents": 5000,
        "currency": "usd",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["amount_cents"] == 5000
    assert data["source"] == "stripe"
    assert "id" in data


async def test_get_revenue_summary(auth_client_with_business):
    c, slug = auth_client_with_business
    # Record two events
    await c.post(f"/api/businesses/{slug}/revenue-tracking/events", json={
        "source": "stripe",
        "amount_cents": 3000,
    })
    await c.post(f"/api/businesses/{slug}/revenue-tracking/events", json={
        "source": "gumroad",
        "amount_cents": 2000,
    })

    resp = await c.get(f"/api/businesses/{slug}/revenue-tracking/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cents"] == 5000
    assert data["total_usd"] == 50.0
    assert data["total_events"] == 2
    assert "stripe" in data["by_source"]
    assert "gumroad" in data["by_source"]


async def test_get_roi(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/revenue-tracking/roi")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_revenue_cents" in data
    assert "estimated_cost_cents" in data
    assert "roi_pct" in data
    assert "plan" in data


async def test_revenue_webhook(auth_client_with_business):
    c, slug = auth_client_with_business
    # Valid payment event
    resp = await c.post(f"/api/businesses/{slug}/revenue-tracking/webhook", json={
        "source": "stripe",
        "event_type": "charge.succeeded",
        "amount_cents": 9900,
        "currency": "usd",
        "metadata": {"utm_source": "google"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert "event_id" in data

    # Ignored event type
    resp2 = await c.post(f"/api/businesses/{slug}/revenue-tracking/webhook", json={
        "source": "stripe",
        "event_type": "customer.updated",
        "amount_cents": 0,
    })
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"


# ---------------------------------------------------------------------------
# Distribution routes
# ---------------------------------------------------------------------------


async def test_add_channel(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.post(f"/api/businesses/{slug}/distribution/channels", json={
        "platform": "twitter",
        "config": {"handle": "@testbiz"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == "twitter"
    assert data["status"] == "active"
    assert "id" in data


async def test_list_channels(auth_client_with_business):
    c, slug = auth_client_with_business
    # Add two channels
    await c.post(f"/api/businesses/{slug}/distribution/channels", json={"platform": "twitter"})
    await c.post(f"/api/businesses/{slug}/distribution/channels", json={"platform": "linkedin"})

    resp = await c.get(f"/api/businesses/{slug}/distribution/channels")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["channels"]) == 2
    platforms = {ch["platform"] for ch in data["channels"]}
    assert platforms == {"twitter", "linkedin"}


async def test_distribution_stats(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/distribution/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "channel_count" in data
    assert "content_distributed" in data
    assert "content_pending" in data
    assert "content_total" in data


# ---------------------------------------------------------------------------
# Advisory routes
# ---------------------------------------------------------------------------


async def test_get_notes(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/advisory/notes")
    assert resp.status_code == 200
    data = resp.json()
    assert "notes" in data
    assert isinstance(data["notes"], list)


async def test_weekly_digest(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/advisory/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert "period" in data
    assert "roadmap_day" in data
    assert "current_phase" in data
    assert "cycles" in data
    assert "content" in data
    assert "milestones" in data
    assert "revenue" in data
    assert data["current_phase"] == 1


async def test_get_warnings(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.get(f"/api/businesses/{slug}/advisory/warnings")
    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert isinstance(data["warnings"], list)


# ---------------------------------------------------------------------------
# Competitor routes
# ---------------------------------------------------------------------------


async def test_add_competitor(auth_client_with_business):
    c, slug = auth_client_with_business
    resp = await c.post(f"/api/businesses/{slug}/competitors", json={
        "name": "Rival Inc",
        "url": "https://rival.com",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Rival Inc"
    assert data["url"] == "https://rival.com"
    assert "id" in data


async def test_list_competitors(auth_client_with_business):
    c, slug = auth_client_with_business
    await c.post(f"/api/businesses/{slug}/competitors", json={"name": "Rival A"})
    await c.post(f"/api/businesses/{slug}/competitors", json={"name": "Rival B", "url": "https://b.com"})

    resp = await c.get(f"/api/businesses/{slug}/competitors")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["competitors"]) == 2
    names = {comp["name"] for comp in data["competitors"]}
    assert names == {"Rival A", "Rival B"}


async def test_competitive_brief(auth_client_with_business):
    c, slug = auth_client_with_business
    # Add a competitor first so brief has data
    await c.post(f"/api/businesses/{slug}/competitors", json={
        "name": "BigCorp",
        "url": "https://bigcorp.io",
    })

    resp = await c.get(f"/api/businesses/{slug}/competitors/brief")
    assert resp.status_code == 200
    data = resp.json()
    assert data["competitors_tracked"] == 1
    assert len(data["competitors"]) == 1
    assert data["competitors"][0]["name"] == "BigCorp"
    assert "generated_at" in data
