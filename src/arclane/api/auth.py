"""Authentication — JWT token verification and business ownership.

Arclane uses JWT tokens issued by the login endpoint. Tokens contain
the user's email which is matched against business.owner_email for
ownership verification.

In development (ARCLANE_AUTH_REQUIRED=false), auth is optional —
requests without tokens still succeed. In production, auth is enforced.
"""

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import Business

log = get_logger("auth")

_auth_required = settings.env == "production"

# Hard guard: refuse to start if auth is explicitly disabled in production
if not _auth_required and settings.env in ("production", "staging"):
    raise RuntimeError(
        "Authentication cannot be disabled in production/staging. "
        "Remove ARCLANE_AUTH_REQUIRED or set ARCLANE_ENV=development."
    )


def get_current_user_email(request: Request) -> str | None:
    """Extract and verify email from JWT Bearer token.

    Returns None if no token is provided and auth is not required.
    Raises 401 if token is invalid or missing when auth is required.
    """
    session = getattr(request, "session", None)
    if isinstance(session, dict):
        session_email = session.get("user_email")
        if session_email:
            return session_email

    auth_header = request.headers.get("Authorization", "")
    token = ""

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    elif not token:
        if _auth_required:
            raise HTTPException(status_code=401, detail="Authentication required")
        return None

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        email = payload.get("email") or payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_owned_business(
    request: Request,
    business_slug: str,
    session: AsyncSession = Depends(get_session),
) -> Business:
    """Get a business and verify the caller owns it.

    In dev mode (auth not required), returns the business without ownership check.
    In production, verifies the JWT email matches business.owner_email.
    """
    result = await session.execute(
        select(Business).where(Business.slug == business_slug)
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    email = get_current_user_email(request)
    if email and email != business.owner_email:
        # Authenticated but not the owner — return 404 (don't reveal existence)
        raise HTTPException(status_code=404, detail="Business not found")

    return business
