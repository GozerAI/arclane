"""Auth — JWT token issuance and Zuultimate proxy.

Login flow:
  1. Try Zuultimate for full identity validation
  2. If Zuultimate is down, fall back to local password hash verification
  3. Frontend stores access_token and sends as Bearer header
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from arclane.api.app import limiter
from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import Business

log = get_logger("auth")
router = APIRouter()

TOKEN_EXPIRY_HOURS = 24


# ── Password hashing ──────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    """Hash a password using scrypt with a random salt."""
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return f"{salt}:{h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored scrypt hash."""
    try:
        salt, hash_hex = stored.split(":", 1)
        h = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
        return hmac.compare_digest(h.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── Pydantic models ───────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    email: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


def _create_token(email: str) -> str:
    payload = {
        "sub": email,
        "email": email,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
):
    """Authenticate user and issue JWT.

    Tries Zuultimate first; falls back to local password verification
    if Zuultimate is unreachable. Requires a password in all cases.
    """
    # Try Zuultimate first
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.zuultimate_base_url}/v1/identity/auth/login",
                json={"email": payload.email, "password": payload.password},
                timeout=10.0,
            )
        if resp.status_code == 200:
            token = _create_token(payload.email)
            return TokenResponse(access_token=token, email=payload.email)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    except httpx.RequestError:
        log.warning("Zuultimate unreachable, falling back to local password auth")

    # Fallback: verify against locally stored password hash
    result = await session.execute(
        select(Business).where(Business.owner_email == payload.email).limit(1)
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not business.password_hash:
        # No local password set — cannot authenticate without Zuultimate
        raise HTTPException(
            status_code=503,
            detail="Authentication service unavailable",
        )

    if not _verify_password(payload.password, business.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _create_token(payload.email)
    log.info("Login successful for %s (local auth)", payload.email)
    return TokenResponse(access_token=token, email=payload.email)


@router.get("/validate")
async def validate(request: Request):
    """Validate a JWT token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

    token = auth_header[7:]
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return {"valid": True, "email": payload.get("email")}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(request: Request, payload: ForgotPasswordRequest):
    """Request a password reset link.

    Always returns success to avoid revealing whether an email exists.
    """
    reset_token = jwt.encode(
        {
            "sub": payload.email,
            "type": "reset",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.secret_key,
        algorithm="HS256",
    )

    try:
        from arclane.notifications import send_password_reset_email

        await send_password_reset_email(payload.email, reset_token)
    except Exception:
        log.debug("Password reset email not sent (notifications module unavailable)")

    log.info("Password reset requested for %s", payload.email)
    return {"message": "If an account exists, a reset link has been sent"}


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
):
    """Complete a password reset using a valid reset token."""
    try:
        decoded = jwt.decode(payload.token, settings.secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Reset token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid reset token")

    if decoded.get("type") != "reset":
        raise HTTPException(status_code=400, detail="Invalid reset token")

    email = decoded.get("sub")

    # Find a business owned by this email and update the password hash
    result = await session.execute(
        select(Business).where(Business.owner_email == email).limit(1)
    )
    business = result.scalar_one_or_none()
    if not business:
        # Don't reveal whether the email exists
        raise HTTPException(status_code=400, detail="Invalid reset token")

    business.password_hash = _hash_password(payload.new_password)
    await session.commit()

    log.info("Password reset completed for %s", email)
    return {"message": "Password updated"}
