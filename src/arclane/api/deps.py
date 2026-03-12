"""Shared API dependencies."""

from fastapi import Depends, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.auth import get_current_user_email
from arclane.core.database import get_session
from arclane.models.tables import Business


async def get_business(
    request: Request,
    business_slug: str = Path(...),
    session: AsyncSession = Depends(get_session),
) -> Business:
    """Get a business by slug with ownership verification.

    In dev mode (auth not required), returns the business without ownership check.
    In production, verifies the JWT email matches business.owner_email.
    """
    result = await session.execute(
        select(Business).where(Business.slug == business_slug)
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    # Verify ownership if authenticated
    email = get_current_user_email(request)
    if email and email != business.owner_email:
        raise HTTPException(status_code=404, detail="Business not found")

    return business
