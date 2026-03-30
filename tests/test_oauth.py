"""Tests for OAuth login flow."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from arclane.api.routes.oauth import _dashboard_url, _find_or_create_user, _frontend_url


# --- Helper tests ---


def test_dashboard_url_plain():
    with patch("arclane.api.routes.oauth.settings") as s:
        s.env = "test"
        assert _dashboard_url() == "http://localhost:8012/dashboard"


def test_dashboard_url_with_error():
    with patch("arclane.api.routes.oauth.settings") as s:
        s.env = "test"
        assert "auth_error=no_email" in _dashboard_url(error="no_email")


def test_dashboard_url_no_token_in_url():
    """OAuth redirects should never include tokens in the URL (httpOnly cookie instead)."""
    with patch("arclane.api.routes.oauth.settings") as s:
        s.env = "test"
        url = _dashboard_url()
        assert "access_token" not in url
        assert url == "http://localhost:8012/dashboard"


@pytest.mark.asyncio
async def test_find_or_create_user_existing(db_session):
    from arclane.models.tables import Business

    biz = Business(
        slug="test-biz", name="Test", description="", owner_email="user@example.com",
    )
    db_session.add(biz)
    await db_session.commit()

    result = await _find_or_create_user("user@example.com", "User", db_session)
    assert result.slug == "test-biz"


@pytest.mark.asyncio
async def test_find_or_create_user_new(db_session):
    result = await _find_or_create_user("new@example.com", "New User", db_session)
    assert result.owner_email == "new@example.com"
    assert result.password_hash is None
    assert result.slug.startswith("_user-")


@pytest.mark.asyncio
async def test_find_or_create_user_no_name(db_session):
    result = await _find_or_create_user("noname@example.com", None, db_session)
    assert result.name == "noname"


# --- Login redirect tests ---


@pytest.mark.asyncio
async def test_google_login_returns_501_when_not_configured():
    from httpx import ASGITransport, AsyncClient
    from arclane.api.app import app

    with patch("arclane.api.routes.oauth.settings") as s:
        s.google_client_id = ""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/auth/login/google", follow_redirects=False)
            assert resp.status_code == 501


@pytest.mark.asyncio
async def test_github_login_returns_501_when_not_configured():
    from httpx import ASGITransport, AsyncClient
    from arclane.api.app import app

    with patch("arclane.api.routes.oauth.settings") as s:
        s.github_client_id = ""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/auth/login/github", follow_redirects=False)
            assert resp.status_code == 501


@pytest.mark.asyncio
async def test_google_callback_no_email_redirects_with_error():
    from httpx import ASGITransport, AsyncClient
    from arclane.api.app import app

    with patch("arclane.api.routes.oauth.settings") as s:
        s.google_client_id = "test-id"
        s.google_client_secret = "test-secret"
        s.env = "test"
        s.domain = "arclane.cloud"

        mock_token = {"userinfo": {"name": "Test User"}}  # no email
        with patch("arclane.api.routes.oauth.oauth") as mock_oauth:
            mock_oauth.google.authorize_access_token = AsyncMock(return_value=mock_token)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/auth/callback/google", follow_redirects=False)
                assert resp.status_code == 307
                assert "auth_error=no_email" in resp.headers["location"]


@pytest.mark.asyncio
async def test_google_callback_success_redirects_to_dashboard():
    """Successful OAuth sets session cookie and redirects without token in URL."""
    from httpx import ASGITransport, AsyncClient
    from arclane.api.app import app

    with patch("arclane.api.routes.oauth.settings") as s:
        s.google_client_id = "test-id"
        s.google_client_secret = "test-secret"
        s.env = "test"
        s.domain = "arclane.cloud"
        s.secret_key = "test-secret-key"

        mock_token = {"userinfo": {"email": "oauth@test.com", "name": "OAuth User"}}
        with (
            patch("arclane.api.routes.oauth.oauth") as mock_oauth,
            patch("arclane.api.routes.oauth._find_or_create_user", new_callable=AsyncMock) as mock_find,
            patch("arclane.api.routes.oauth._set_browser_session"),
        ):
            mock_oauth.google.authorize_access_token = AsyncMock(return_value=mock_token)
            mock_find.return_value = MagicMock(owner_email="oauth@test.com")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/auth/callback/google", follow_redirects=False)
                assert resp.status_code == 307
                location = resp.headers["location"]
                assert location.endswith("/dashboard")
                assert "access_token" not in location


@pytest.mark.asyncio
async def test_github_callback_no_email_redirects_with_error():
    from httpx import ASGITransport, AsyncClient
    from arclane.api.app import app

    with patch("arclane.api.routes.oauth.settings") as s:
        s.github_client_id = "test-id"
        s.github_client_secret = "test-secret"
        s.env = "test"
        s.domain = "arclane.cloud"

        mock_token = {"access_token": "gh-tok"}
        mock_user_resp = MagicMock()
        mock_user_resp.json.return_value = {"login": "testuser"}  # no email
        mock_emails_resp = MagicMock()
        mock_emails_resp.json.return_value = []  # no emails

        with patch("arclane.api.routes.oauth.oauth") as mock_oauth:
            mock_oauth.github.authorize_access_token = AsyncMock(return_value=mock_token)
            mock_oauth.github.get = AsyncMock(side_effect=[mock_user_resp, mock_emails_resp])
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/auth/callback/github", follow_redirects=False)
                assert resp.status_code == 307
                assert "auth_error=no_email" in resp.headers["location"]
