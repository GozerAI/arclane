"""Auth edge cases — JWT lifecycle, password reset, rate limiting, CORS, slugs."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.config import settings
from arclane.core.database import get_session
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
    """Authenticated test client."""
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
            reg = await c.post("/api/auth/register", json={
                "email": "auth@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})
            yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# --- JWT token generation ---


async def test_register_returns_valid_jwt(client):
    """Registration returns a decodable JWT with email claim."""
    resp = await client.post("/api/auth/register", json={
        "email": "jwt@example.com",
        "password": "securepass123",
    })
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    decoded = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert decoded["email"] == "jwt@example.com"
    assert decoded["sub"] == "jwt@example.com"


async def test_token_contains_expiry(client):
    """JWT contains an exp claim in the future."""
    resp = await client.post("/api/auth/register", json={
        "email": "expiry@example.com",
        "password": "securepass123",
    })
    token = resp.json()["access_token"]
    decoded = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert "exp" in decoded
    exp_dt = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
    assert exp_dt > datetime.now(timezone.utc)


async def test_token_contains_iat(client):
    """JWT contains an iat (issued at) claim."""
    resp = await client.post("/api/auth/register", json={
        "email": "iat@example.com",
        "password": "securepass123",
    })
    token = resp.json()["access_token"]
    decoded = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert "iat" in decoded


# --- JWT token validation ---


async def test_validate_valid_token(client):
    """Validate endpoint accepts a valid token."""
    resp = await client.post("/api/auth/register", json={
        "email": "valid@example.com",
        "password": "securepass123",
    })
    token = resp.json()["access_token"]
    validate_resp = await client.get(
        "/api/auth/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert validate_resp.status_code == 200
    assert validate_resp.json()["valid"] is True
    assert validate_resp.json()["email"] == "valid@example.com"


async def test_validate_missing_token(client):
    """Validate endpoint rejects request without token."""
    resp = await client.get("/api/auth/validate")
    assert resp.status_code == 401
    assert "missing" in resp.json()["detail"].lower()


# --- Expired token rejection ---


async def test_expired_token_rejected_on_validate(client):
    """Expired JWT is rejected by validate endpoint."""
    token = jwt.encode(
        {
            "sub": "expired@example.com",
            "email": "expired@example.com",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    resp = await client.get(
        "/api/auth/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


# --- Invalid token rejection ---


async def test_invalid_token_rejected(client):
    """Malformed JWT is rejected."""
    resp = await client.get(
        "/api/auth/validate",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"].lower()


async def test_wrong_secret_token_rejected(client):
    """JWT signed with wrong secret is rejected."""
    token = jwt.encode(
        {
            "sub": "wrong@example.com",
            "email": "wrong@example.com",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "wrong-secret-key",
        algorithm="HS256",
    )
    resp = await client.get(
        "/api/auth/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


async def test_token_without_email_is_rejected(client):
    """JWT without email or sub claim is rejected by auth middleware."""
    from arclane.api.auth import get_current_user_email
    from fastapi import HTTPException

    token = jwt.encode(
        {
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )

    class FakeRequest:
        headers = {"Authorization": f"Bearer {token}"}

    # In non-production mode, the auth module won't raise for missing tokens
    # but should raise for tokens without email/sub
    with pytest.raises(HTTPException) as exc_info:
        get_current_user_email(FakeRequest())
    assert exc_info.value.status_code == 401


# --- Password reset flow ---


async def test_reset_flow_full_cycle(client):
    """Full password reset: register -> forgot -> reset -> login."""
    # Register
    await client.post("/api/auth/register", json={
        "email": "resetflow@example.com",
        "password": "oldpassword123",
    })

    # Forgot password
    resp = await client.post("/api/auth/forgot-password", json={
        "email": "resetflow@example.com",
    })
    assert resp.status_code == 200

    # Generate a reset token manually
    reset_token = jwt.encode(
        {
            "sub": "resetflow@example.com",
            "type": "reset",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )

    # Reset password
    resp = await client.post("/api/auth/reset-password", json={
        "token": reset_token,
        "new_password": "newpassword456",
    })
    assert resp.status_code == 200

    # Login with new password (mocking Zuultimate as unreachable)
    import httpx
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("unreachable"))

    with patch("arclane.api.routes.auth.httpx.AsyncClient", return_value=mock_client):
        login_resp = await client.post("/api/auth/login", json={
            "email": "resetflow@example.com",
            "password": "newpassword456",
        })
    assert login_resp.status_code == 200


async def test_reset_token_wrong_type_rejected(client):
    """Reset token with wrong type claim is rejected."""
    token = jwt.encode(
        {
            "sub": "wrongtype@example.com",
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    resp = await client.post("/api/auth/reset-password", json={
        "token": token,
        "new_password": "newpassword123",
    })
    assert resp.status_code == 400
    assert "invalid" in resp.json()["detail"].lower()


async def test_reset_expired_token_rejected(client):
    """Expired reset token is rejected."""
    token = jwt.encode(
        {
            "sub": "expired@example.com",
            "type": "reset",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    resp = await client.post("/api/auth/reset-password", json={
        "token": token,
        "new_password": "newpassword123",
    })
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


async def test_reset_too_short_password(client):
    """Reset with password shorter than 8 chars is rejected."""
    token = jwt.encode(
        {
            "sub": "short@example.com",
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


# --- Security headers ---


async def test_security_headers_on_api(client):
    """API responses include security headers."""
    resp = await client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_csp_header_blocks_framing(client):
    """CSP includes frame-ancestors none."""
    resp = await client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp


async def test_permissions_policy_header(client):
    """Permissions-Policy restricts camera, mic, geolocation."""
    resp = await client.get("/health")
    pp = resp.headers.get("permissions-policy", "")
    assert "camera=()" in pp
    assert "microphone=()" in pp
    assert "geolocation=()" in pp


# --- Reserved slug protection ---


async def test_reserved_slug_dashboard(auth_client):
    """Cannot create business with slug 'dashboard'."""
    resp = await auth_client.post("/api/businesses", json={
        "name": "Dashboard",
        "description": "Trying to claim dashboard",
    })
    assert resp.status_code == 400
    assert "reserved" in resp.json()["detail"].lower()


async def test_reserved_slug_login(auth_client):
    """Cannot create business with slug 'login'."""
    resp = await auth_client.post("/api/businesses", json={
        "name": "Login",
        "description": "Trying to claim login",
    })
    assert resp.status_code == 400


async def test_reserved_slug_support(auth_client):
    """Cannot create business with slug 'support'."""
    resp = await auth_client.post("/api/businesses", json={
        "name": "Support",
        "description": "Trying to claim support",
    })
    assert resp.status_code == 400


async def test_reserved_slug_cdn(auth_client):
    """Cannot create business with slug 'cdn'."""
    resp = await auth_client.post("/api/businesses", json={
        "name": "CDN",
        "description": "Trying to claim cdn",
    })
    assert resp.status_code == 400


async def test_non_reserved_slug_allowed(auth_client):
    """Normal business name is accepted."""
    resp = await auth_client.post("/api/businesses", json={
        "name": "My Cool Business",
        "description": "A normal business",
    })
    assert resp.status_code == 201


# --- Password hashing ---


def test_password_hash_and_verify():
    """Password hashing and verification work correctly."""
    from arclane.api.routes.auth import _hash_password, _verify_password

    password = "my-secure-password"
    hashed = _hash_password(password)

    assert ":" in hashed
    assert _verify_password(password, hashed) is True
    assert _verify_password("wrong-password", hashed) is False


def test_password_hash_is_unique():
    """Same password produces different hashes (random salt)."""
    from arclane.api.routes.auth import _hash_password

    h1 = _hash_password("samepassword")
    h2 = _hash_password("samepassword")
    assert h1 != h2


def test_verify_malformed_hash():
    """Malformed hash returns False, not an exception."""
    from arclane.api.routes.auth import _verify_password

    assert _verify_password("password", "not-a-valid-hash") is False
    assert _verify_password("password", "") is False
    assert _verify_password("password", "no-colon-here") is False


# --- Duplicate registration ---


async def test_duplicate_registration(client):
    """Registering twice with same email returns 409."""
    payload = {"email": "dup@example.com", "password": "securepass123"}
    resp1 = await client.post("/api/auth/register", json=payload)
    assert resp1.status_code == 201
    resp2 = await client.post("/api/auth/register", json=payload)
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"].lower()


# --- Login edge cases ---


async def test_login_nonexistent_email(client):
    """Login with unregistered email returns 401."""
    import httpx
    mock_hc = AsyncMock()
    mock_hc.__aenter__ = AsyncMock(return_value=mock_hc)
    mock_hc.__aexit__ = AsyncMock(return_value=None)
    mock_hc.post = AsyncMock(side_effect=httpx.RequestError("unreachable"))

    with patch("arclane.api.routes.auth.httpx.AsyncClient", return_value=mock_hc):
        resp = await client.post("/api/auth/login", json={
            "email": "nobody@example.com",
            "password": "anypassword123",
        })
    assert resp.status_code == 401


async def test_login_wrong_password(client):
    """Login with wrong password returns 401."""
    await client.post("/api/auth/register", json={
        "email": "wrongpw@example.com",
        "password": "correctpassword123",
    })

    import httpx
    mock_hc = AsyncMock()
    mock_hc.__aenter__ = AsyncMock(return_value=mock_hc)
    mock_hc.__aexit__ = AsyncMock(return_value=None)
    mock_hc.post = AsyncMock(side_effect=httpx.RequestError("unreachable"))

    with patch("arclane.api.routes.auth.httpx.AsyncClient", return_value=mock_hc):
        resp = await client.post("/api/auth/login", json={
            "email": "wrongpw@example.com",
            "password": "wrongpassword",
        })
    assert resp.status_code == 401
