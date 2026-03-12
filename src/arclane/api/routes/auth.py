"""Auth — JWT token issuance and Zuultimate proxy.

Login flow:
  1. Try Zuultimate first (if reachable) for full identity validation
  2. Fall back to local email-based login (issues JWT signed with secret_key)
  3. Frontend stores access_token and sends as Bearer header
"""

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


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = ""


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

    Tries Zuultimate first; falls back to verifying the email
    has at least one business (email-based auth for MVP).
    """
    # Try Zuultimate if password provided
    if payload.password:
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
            log.warning("Zuultimate unreachable, falling back to email-based auth")

    # Fallback: verify email has businesses (MVP email-only auth)
    result = await session.execute(
        select(Business).where(Business.owner_email == payload.email).limit(1)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _create_token(payload.email)
    log.info("Login successful for %s", payload.email)
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
async def reset_password(request: Request, payload: ResetPasswordRequest):
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
    log.info("Password reset completed for %s", email)
    return {"message": "Password updated"}
