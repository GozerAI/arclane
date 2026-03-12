"""Test API endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base


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

    # Mock the background cycle runner so it doesn't call engine
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


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "service" not in data  # health endpoint no longer leaks service name


async def test_create_business(client):
    resp = await client.post("/api/businesses", json={
        "name": "Test Biz",
        "description": "A test business for unit tests",
        "owner_email": "test@example.com",
        "template": "content-site",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "test-biz"
    assert data["name"] == "Test Biz"
    assert data["plan"] == "starter"
    assert "arclane.cloud" in data["subdomain"]


async def test_create_duplicate_business(client):
    payload = {
        "name": "Dupe Test",
        "description": "Testing duplicates",
        "owner_email": "test@example.com",
    }
    resp1 = await client.post("/api/businesses", json=payload)
    assert resp1.status_code == 201

    resp2 = await client.post("/api/businesses", json=payload)
    assert resp2.status_code == 409


async def test_list_businesses(client):
    await client.post("/api/businesses", json={
        "name": "Biz One",
        "description": "First",
        "owner_email": "test@example.com",
    })
    await client.post("/api/businesses", json={
        "name": "Biz Two",
        "description": "Second",
        "owner_email": "test@example.com",
    })
    resp = await client.get("/api/businesses?owner_email=test@example.com")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_get_feed_empty(client):
    await client.post("/api/businesses", json={
        "name": "Feed Test",
        "description": "Testing feed",
        "owner_email": "test@example.com",
    })
    resp = await client.get("/api/businesses/feed-test/feed")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_content_empty(client):
    await client.post("/api/businesses", json={
        "name": "Content Test",
        "description": "Testing content",
        "owner_email": "test@example.com",
    })
    resp = await client.get("/api/businesses/content-test/content")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_metrics_empty(client):
    await client.post("/api/businesses", json={
        "name": "Metric Test",
        "description": "Testing metrics",
        "owner_email": "test@example.com",
    })
    resp = await client.get("/api/businesses/metric-test/metrics")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_settings(client):
    await client.post("/api/businesses", json={
        "name": "Settings Test",
        "description": "Testing settings",
        "owner_email": "test@example.com",
    })
    resp = await client.get("/api/businesses/settings-test/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Settings Test"
    assert data["plan"] == "starter"


async def test_update_settings(client):
    await client.post("/api/businesses", json={
        "name": "Update Test",
        "description": "Original",
        "owner_email": "test@example.com",
    })
    resp = await client.patch("/api/businesses/update-test/settings", json={
        "description": "Updated description",
    })
    assert resp.status_code == 200
    assert resp.json()["description"] == "Updated description"


async def test_trigger_cycle(client):
    await client.post("/api/businesses", json={
        "name": "Cycle Test",
        "description": "Testing cycles",
        "owner_email": "test@example.com",
    })
    resp = await client.post("/api/businesses/cycle-test/cycles", json={
        "task_description": "Write a blog post about AI",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["trigger"] == "on_demand"
    assert data["status"] == "pending"


async def test_trigger_cycle_no_credits(client):
    await client.post("/api/businesses", json={
        "name": "No Credits",
        "description": "Testing no credits",
        "owner_email": "test@example.com",
    })
    # Exhaust remaining 14 credits (1 already used by initial cycle)
    for i in range(14):
        resp = await client.post("/api/businesses/no-credits/cycles", json={})
        assert resp.status_code == 201

    # Next should fail
    resp = await client.post("/api/businesses/no-credits/cycles", json={})
    assert resp.status_code == 402


async def test_list_cycles(client):
    await client.post("/api/businesses", json={
        "name": "List Cycles",
        "description": "Testing",
        "owner_email": "test@example.com",
    })
    await client.post("/api/businesses/list-cycles/cycles", json={"task_description": "Task 1"})
    await client.post("/api/businesses/list-cycles/cycles", json={"task_description": "Task 2"})

    resp = await client.get("/api/businesses/list-cycles/cycles")
    assert resp.status_code == 200
    assert len(resp.json()) == 3  # 1 initial + 2 on-demand


async def test_nonexistent_business(client):
    resp = await client.get("/api/businesses/does-not-exist/feed")
    assert resp.status_code == 404
