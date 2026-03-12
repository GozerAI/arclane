"""Test password reset flow."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.config import settings
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


async def test_forgot_password_returns_success(client):
    resp = await client.post("/api/auth/forgot-password", json={
        "email": "user@example.com",
    })
    assert resp.status_code == 200
    assert resp.json()["message"] == "If an account exists, a reset link has been sent"


async def test_forgot_password_invalid_email_format(client):
    resp = await client.post("/api/auth/forgot-password", json={
        "email": "not-an-email",
    })
    assert resp.status_code == 422


async def test_reset_password_valid_token(client):
    token = jwt.encode(
        {
            "sub": "user@example.com",
            "type": "reset",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    resp = await client.post("/api/auth/reset-password", json={
        "token": token,
        "new_password": "newsecurepassword",
    })
    assert resp.status_code == 200
    assert resp.json()["message"] == "Password updated"


async def test_reset_password_expired_token(client):
    token = jwt.encode(
        {
            "sub": "user@example.com",
            "type": "reset",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    resp = await client.post("/api/auth/reset-password", json={
        "token": token,
        "new_password": "newsecurepassword",
    })
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


async def test_reset_password_wrong_type(client):
    token = jwt.encode(
        {
            "sub": "user@example.com",
            "email": "user@example.com",
            "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    resp = await client.post("/api/auth/reset-password", json={
        "token": token,
        "new_password": "newsecurepassword",
    })
    assert resp.status_code == 400
    assert "invalid" in resp.json()["detail"].lower()


async def test_reset_password_short_password(client):
    token = jwt.encode(
        {
            "sub": "user@example.com",
            "type": "reset",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    resp = await client.post("/api/auth/reset-password", json={
        "token": token,
        "new_password": "short",
    })
    assert resp.status_code == 422
