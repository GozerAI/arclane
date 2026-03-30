"""HTTP integration tests for the newer API routes (forecast, repurpose, webhooks, etc.)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base, Business, Content


@pytest.fixture
async def auth_client_with_business():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override

    from arclane.api.routes import cycles as cycles_module

    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            reg = await c.post(
                "/api/auth/register",
                json={"email": "routes@example.com", "password": "testpass123"},
            )
            token = reg.json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})
            resp = await c.post(
                "/api/businesses",
                json={"description": "Route test business"},
            )
            assert resp.status_code == 201, resp.text
            slug = resp.json()["slug"]
            yield c, slug

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class TestForecastRoutes:
    async def test_get_forecast(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/forecast")
        assert resp.status_code == 200
        data = resp.json()
        assert "velocity" in data
        assert "graduation_eta" in data
        assert "pace" in data

    async def test_get_pace(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/forecast/pace")
        assert resp.status_code == 200
        data = resp.json()
        assert "pace" in data
        assert "velocity" in data

    async def test_get_bottlenecks(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/forecast/bottlenecks")
        assert resp.status_code == 200
        data = resp.json()
        assert "bottlenecks" in data
        assert "weekly_focus" in data


class TestWebhookRoutes:
    async def test_ingest_lead(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.post(
            f"/api/businesses/{slug}/webhooks/leads",
            json={
                "source": "google_ads",
                "name": "John Doe",
                "email": "john@example.com",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_ingest_custom_metric(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.post(
            f"/api/businesses/{slug}/webhooks/metrics",
            json={
                "name": "signups",
                "value": 42,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == 42

    async def test_ingest_revenue_with_attribution(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.post(
            f"/api/businesses/{slug}/webhooks/revenue",
            json={
                "source": "stripe",
                "amount_cents": 4900,
                "utm_source": "google",
                "utm_campaign": "launch",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount_cents"] == 4900
        assert data["status"] == "ok"
        assert "event_id" in data


class TestBenchmarkRoutes:
    async def test_get_benchmarks(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/benchmarks")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data


class TestContentAnalyticsRoutes:
    async def test_get_insights(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/content-analytics/insights")
        assert resp.status_code == 200
        assert "insights" in resp.json()

    async def test_get_top_content(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/content-analytics/top")
        assert resp.status_code == 200
        assert "top" in resp.json()

    async def test_get_by_type(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/content-analytics/by-type")
        assert resp.status_code == 200
        assert "by_type" in resp.json()


class TestPhaseUpsells:
    async def test_phase_suggestions(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.get(f"/api/businesses/{slug}/upsell/phase-suggestions")
        assert resp.status_code == 200
        data = resp.json()
        assert "phase" in data
        assert "suggestions" in data


class TestAutoFillContent:
    async def test_auto_fill(self, auth_client_with_business):
        c, slug = auth_client_with_business
        resp = await c.post(
            f"/api/businesses/{slug}/content/auto-fill?days_ahead=7&max_drafts=2"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "created" in data
        assert "drafts" in data
