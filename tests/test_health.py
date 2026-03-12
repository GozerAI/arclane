"""Tests for health endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base


@pytest.fixture
async def client():
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
            yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_basic_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_detailed_health_db_ok(client):
    with patch("arclane.core.database.check_db_health", new_callable=AsyncMock, return_value=True):
        resp = await client.get("/health/detailed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks"]["database"] is True
    assert "csuite" in data["checks"]
    assert "zuultimate" in data["checks"]


async def test_detailed_health_degraded(client):
    with patch("arclane.core.database.check_db_health", new_callable=AsyncMock, return_value=False):
        resp = await client.get("/health/detailed")
    data = resp.json()
    assert data["status"] == "degraded"
