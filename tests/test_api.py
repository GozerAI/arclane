"""Test API endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.engine.website_intelligence import WebsiteSnapshot
from arclane.models.tables import Base, Business


@pytest.fixture
async def client():
    """Test client with in-memory DB and mocked orchestrator."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    # Mock the background cycle runner so it doesn't call C-Suite
    from arclane.api.routes import cycles as cycles_module
    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def auth_client():
    """Authenticated test client — registers a user and injects JWT header."""
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
            # Register a test user
            reg = await c.post("/api/auth/register", json={
                "email": "test@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})
            yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "service" not in data  # health endpoint no longer leaks service name


async def test_marketing_pages_are_served(client):
    for path, expected in [
        ("/features", "Features - Arclane"),
        ("/pricing", "Pricing"),
        ("/about", "About"),
        ("/contact", "Contact"),
        ("/terms", "Terms"),
        ("/privacy", "Privacy"),
    ]:
        resp = await client.get(path)
        assert resp.status_code == 200
        assert expected in resp.text


async def test_create_business(auth_client):
    resp = await auth_client.post("/api/businesses", json={
        "name": "Test Biz",
        "description": "A test business for unit tests",
        "owner_email": "test@example.com",
        "template": "content-site",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "test-biz"
    assert data["name"] == "Test Biz"
    assert data["plan"] == "preview"
    assert "arclane.cloud" in data["subdomain"]


async def test_create_duplicate_business(auth_client):
    payload = {
        "name": "Dupe Test",
        "description": "Testing duplicates",
        "owner_email": "test@example.com",
    }
    resp1 = await auth_client.post("/api/businesses", json=payload)
    assert resp1.status_code == 201

    resp2 = await auth_client.post("/api/businesses", json=payload)
    assert resp2.status_code == 402


async def test_list_businesses(auth_client):
    first = await auth_client.post("/api/businesses", json={
        "name": "Biz One",
        "description": "First",
        "owner_email": "test@example.com",
    })
    second = await auth_client.post("/api/businesses", json={
        "name": "Biz Two",
        "description": "Second",
        "owner_email": "test@example.com",
    })
    assert first.status_code == 201
    assert second.status_code == 402
    resp = await auth_client.get("/api/businesses")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_feed_includes_launch_activity(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "Feed Test",
        "description": "Testing feed",
        "owner_email": "test@example.com",
    })
    resp = await auth_client.get("/api/businesses/feed-test/feed")
    assert resp.status_code == 200
    items = resp.json()
    actions = {item["action"] for item in items}
    assert "Business launched" in actions
    assert "Operating plan prepared" in actions


async def test_get_content_empty(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "Content Test",
        "description": "Testing content",
        "owner_email": "test@example.com",
    })
    resp = await auth_client.get("/api/businesses/content-test/content")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_publish_content_updates_status_and_metrics(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "Publish Test",
        "description": "Testing publish flow",
        "owner_email": "test@example.com",
    })

    from arclane.api.routes import cycles as cycles_module
    with patch.object(cycles_module, "_run_cycle", AsyncMock()):
        pass

    from arclane.core.database import get_session
    override = app.dependency_overrides[get_session]
    async for session in override():
        from arclane.models.tables import Business, Content
        from sqlalchemy import select

        business = (
            await session.execute(select(Business).where(Business.slug == "publish-test"))
        ).scalar_one()
        content = Content(
            business_id=business.id,
            content_type="blog",
            title="Test Draft",
            body="Draft body",
            status="draft",
        )
        session.add(content)
        await session.commit()
        content_id = content.id
        break

    resp = await auth_client.patch(
        f"/api/businesses/publish-test/content/{content_id}",
        json={"status": "published"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"
    assert resp.json()["published_at"] is not None

    metrics_resp = await auth_client.get("/api/businesses/publish-test/metrics")
    assert metrics_resp.status_code == 200
    metric_names = {item["name"] for item in metrics_resp.json()}
    assert "content_total" in metric_names
    assert "content_published" in metric_names


async def test_get_metrics_empty(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "Metric Test",
        "description": "Testing metrics",
        "owner_email": "test@example.com",
    })
    resp = await auth_client.get("/api/businesses/metric-test/metrics")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_settings(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "Settings Test",
        "description": "Testing settings",
        "owner_email": "test@example.com",
    })
    resp = await auth_client.get("/api/businesses/settings-test/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Settings Test"
    assert data["plan"] == "preview"
    assert data["contact_email"] == "settings-test@arclane.cloud"
    assert data["operating_plan"]["agent_tasks"]
    assert data["operating_plan"]["user_recommendations"]
    assert data["operating_plan"]["code_storage"]["workspace_path"].endswith("settings-test")


async def test_update_settings(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "Update Test",
        "description": "Original",
        "owner_email": "test@example.com",
    })
    resp = await auth_client.patch("/api/businesses/update-test/settings", json={
        "description": "Updated description",
    })
    assert resp.status_code == 200
    assert resp.json()["description"] == "Updated description"


async def test_queue_add_on_endpoint_updates_operating_plan():
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
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post("/api/auth/register", json={
                "email": "addons@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            client.headers.update({"Authorization": f"Bearer {token}"})

            created = await client.post("/api/businesses", json={
                "name": "Add On Queue",
                "description": "An automation service for local contractors",
            })
            assert created.status_code == 201

            async with session_factory() as session:
                business = await session.scalar(select(Business).where(Business.slug == "add-on-queue"))
                plan = dict(business.agent_config or {})
                operating_plan = plan["operating_plan"]
                for offer in operating_plan["add_on_offers"]:
                    if offer["key"] == "deep-market-dive":
                        offer["status"] = "available"
                business.agent_config = {"operating_plan": operating_plan}
                flag_modified(business, "agent_config")
                await session.commit()

            resp = await client.post("/api/businesses/add-on-queue/settings/add-ons/deep-market-dive")
            assert resp.status_code == 200
            data = resp.json()
            assert data["operating_plan"]["agent_tasks"][0]["key"] == "addon-market-01"
            assert data["operating_plan"]["agent_tasks"][0]["included_cycles_remaining"] == 3
            assert data["operating_plan"]["add_on_offers"][0]["status"] == "purchased"

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_create_business_from_website(auth_client):
    with patch(
        "arclane.api.routes.intake.fetch_website_snapshot",
        new_callable=AsyncMock,
        return_value=WebsiteSnapshot(
            requested_url="https://example.com",
            final_url="https://example.com",
            title="Example Co",
            headings=["Operator software"],
            excerpt="We help operators streamline field service scheduling.",
        ),
    ):
        resp = await auth_client.post("/api/businesses", json={
            "website_url": "https://example.com",
            "owner_email": "test@example.com",
        })

    assert resp.status_code == 201
    data = resp.json()
    assert data["website_url"] == "https://example.com/"
    assert data["name"] == "Example Co"
    assert "Example Co" in data["description"]


async def test_create_business_generates_name_from_description(auth_client):
    resp = await auth_client.post("/api/businesses", json={
        "description": "An automation service for local businesses that reduces manual follow-up.",
        "owner_email": "test@example.com",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"]
    assert data["slug"]


async def test_trigger_cycle(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "Cycle Test",
        "description": "Testing cycles",
        "owner_email": "test@example.com",
    })
    resp = await auth_client.post("/api/businesses/cycle-test/cycles", json={
        "task_description": "Write a blog post about AI",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["trigger"] == "on_demand"
    assert data["status"] == "pending"


async def test_trigger_cycle_no_credits(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "No Credits",
        "description": "Testing no credits",
        "owner_email": "test@example.com",
    })
    # Exhaust remaining 2 preview credits (1 preview credit was used by the initial cycle)
    for i in range(2):
        resp = await auth_client.post("/api/businesses/no-credits/cycles", json={})
        assert resp.status_code == 201

    # Next should fail
    resp = await auth_client.post("/api/businesses/no-credits/cycles", json={})
    assert resp.status_code == 402


async def test_list_cycles(auth_client):
    await auth_client.post("/api/businesses", json={
        "name": "List Cycles",
        "description": "Testing",
        "owner_email": "test@example.com",
    })
    await auth_client.post("/api/businesses/list-cycles/cycles", json={"task_description": "Task 1"})
    await auth_client.post("/api/businesses/list-cycles/cycles", json={"task_description": "Task 2"})

    resp = await auth_client.get("/api/businesses/list-cycles/cycles")
    assert resp.status_code == 200
    assert len(resp.json()) == 3  # 1 initial + 2 on-demand


async def test_nonexistent_business(auth_client):
    resp = await auth_client.get("/api/businesses/does-not-exist/feed")
    assert resp.status_code == 404
