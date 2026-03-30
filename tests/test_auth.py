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


async def test_register_creates_account_and_returns_token(client):
    resp = await client.post("/api/auth/register", json={
        "email": "newuser@example.com",
        "password": "securepassword123",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["email"] == "newuser@example.com"

    validate = await client.get("/api/auth/validate")
    assert validate.status_code == 200
    assert validate.json()["email"] == "newuser@example.com"


async def test_register_with_name(client):
    resp = await client.post("/api/auth/register", json={
        "email": "named@example.com",
        "password": "securepassword123",
        "name": "Test User",
    })
    assert resp.status_code == 201
    assert resp.json()["email"] == "named@example.com"


async def test_register_duplicate_email_returns_409(client):
    payload = {"email": "dup@example.com", "password": "securepassword123"}
    await client.post("/api/auth/register", json=payload)
    resp = await client.post("/api/auth/register", json=payload)
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"].lower()


async def test_register_weak_password_returns_422(client):
    resp = await client.post("/api/auth/register", json={
        "email": "weak@example.com",
        "password": "short",
    })
    assert resp.status_code == 422


async def test_register_invalid_email_returns_422(client):
    resp = await client.post("/api/auth/register", json={
        "email": "not-an-email",
        "password": "securepassword123",
    })
    assert resp.status_code == 422


async def test_register_then_login_with_local_fallback(client):
    # Register
    reg_resp = await client.post("/api/auth/register", json={
        "email": "localauth@example.com",
        "password": "mypassword123",
    })
    assert reg_resp.status_code == 201

    # Login using local fallback (Zuultimate unreachable in test env)
    import httpx
    from unittest.mock import patch, AsyncMock
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("unreachable"))

    with patch("arclane.api.routes.auth.httpx.AsyncClient", return_value=mock_client):
        login_resp = await client.post("/api/auth/login", json={
            "email": "localauth@example.com",
            "password": "mypassword123",
        })
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()

    validate = await client.get("/api/auth/validate")
    assert validate.status_code == 200
    assert validate.json()["email"] == "localauth@example.com"


async def test_register_then_login_wrong_password(client):
    await client.post("/api/auth/register", json={
        "email": "wrongpw@example.com",
        "password": "correctpassword123",
    })

    import httpx
    from unittest.mock import patch, AsyncMock
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("unreachable"))

    with patch("arclane.api.routes.auth.httpx.AsyncClient", return_value=mock_client):
        login_resp = await client.post("/api/auth/login", json={
            "email": "wrongpw@example.com",
            "password": "wrongpassword",
        })
    assert login_resp.status_code == 401


async def test_logout_clears_browser_session(client):
    reg = await client.post("/api/auth/register", json={
        "email": "logout@example.com",
        "password": "securepassword123",
    })
    assert reg.status_code == 201

    validate = await client.get("/api/auth/validate")
    assert validate.status_code == 200

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    validate = await client.get("/api/auth/validate")
    assert validate.status_code == 401
