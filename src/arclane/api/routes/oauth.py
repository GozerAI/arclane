"""OAuth — Google and GitHub social login."""

import re
import time

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.routes.auth import _set_browser_session
from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import Business

log = get_logger("oauth")
router = APIRouter()

# ── OAuth client setup ───────────────────────────────────────────────────

oauth = OAuth()

# Google (OpenID Connect — email comes from userinfo)
if settings.google_client_id:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# GitHub (OAuth2 — email comes from /user endpoint)
if settings.github_client_id:
    oauth.register(
        name="github",
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        authorize_url="https://github.com/login/oauth/authorize",
        access_token_url="https://github.com/login/oauth/access_token",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "user:email"},
    )


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_redirect_uri(request: Request, provider: str) -> str:
    """Build the OAuth callback URL from the current request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}/api/auth/callback/{provider}"


async def _find_or_create_user(
    email: str,
    name: str | None,
    session: AsyncSession,
) -> Business:
    """Find existing user by email or create a new stub."""
    result = await session.execute(
        select(Business).where(Business.owner_email == email).limit(1)
    )
    business = result.scalar_one_or_none()
    if business:
        return business

    # Create stub (same pattern as register endpoint)
    slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:40]
    slug = f"_user-{slug_base}"

    existing_slug = await session.execute(
        select(Business).where(Business.slug == slug).limit(1)
    )
    if existing_slug.scalar_one_or_none():
        slug = f"_user-{slug_base}-{int(time.time()) % 10000}"

    business = Business(
        slug=slug,
        name=name or email.split("@")[0],
        description="",
        owner_email=email,
        password_hash=None,  # OAuth users have no local password
    )
    session.add(business)
    await session.commit()
    log.info("New OAuth account created for %s", email)
    return business


def _frontend_url() -> str:
    """Build the frontend base URL."""
    if settings.env == "production":
        return f"https://{settings.domain}"
    return "http://localhost:8012"


def _dashboard_url(error: str | None = None) -> str:
    base = f"{_frontend_url()}/dashboard"
    if error:
        return f"{base}?auth_error={error}"
    return base


# ── Google ───────────────────────────────────────────────────────────────

@router.get("/login/google")
async def login_google(request: Request):
    """Redirect to Google consent screen."""
    if not settings.google_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    redirect_uri = _build_redirect_uri(request, "google")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback/google")
async def callback_google(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Handle Google OAuth callback."""
    if not settings.google_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        log.error("Google OAuth token exchange failed: %s", exc)
        return RedirectResponse(_dashboard_url("oauth_failed"))

    userinfo = token.get("userinfo")
    if not userinfo:
        # Fallback: fetch from userinfo endpoint
        resp = await oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
        userinfo = resp.json()

    email = userinfo.get("email")
    if not email:
        return RedirectResponse(_dashboard_url("no_email"))

    name = userinfo.get("name") or userinfo.get("given_name")
    await _find_or_create_user(email, name, session)
    _set_browser_session(request, email)
    log.info("Google OAuth login for %s", email)
    return RedirectResponse(_dashboard_url())


# ── GitHub ───────────────────────────────────────────────────────────────

@router.get("/login/github")
async def login_github(request: Request):
    """Redirect to GitHub authorization."""
    if not settings.github_client_id:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured")
    redirect_uri = _build_redirect_uri(request, "github")
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/callback/github")
async def callback_github(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Handle GitHub OAuth callback."""
    if not settings.github_client_id:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured")

    try:
        token = await oauth.github.authorize_access_token(request)
    except Exception as exc:
        log.error("GitHub OAuth token exchange failed: %s", exc)
        return RedirectResponse(_dashboard_url("oauth_failed"))

    # Get user info from GitHub API
    resp = await oauth.github.get("user", token=token)
    user_data = resp.json()

    email = user_data.get("email")

    # GitHub may not return email in /user — fetch from /user/emails
    if not email:
        emails_resp = await oauth.github.get("user/emails", token=token)
        emails = emails_resp.json()
        primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
        if primary:
            email = primary["email"]

    if not email:
        return RedirectResponse(_dashboard_url("no_email"))

    name = user_data.get("name") or user_data.get("login")
    await _find_or_create_user(email, name, session)
    _set_browser_session(request, email)
    log.info("GitHub OAuth login for %s", email)
    return RedirectResponse(_dashboard_url())
