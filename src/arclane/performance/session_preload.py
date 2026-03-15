"""User session data preloading.

Item 57: Preloads commonly needed session data (business, credits, plan)
in a single query at request start, avoiding N+1 queries during handling.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger

log = get_logger("performance.session_preload")


@dataclass
class PreloadedSession:
    """Preloaded user session data for a single request."""
    email: str | None = None
    business_id: int | None = None
    business_slug: str | None = None
    business_name: str | None = None
    plan: str | None = None
    credits_remaining: int = 0
    credits_bonus: int = 0
    template: str | None = None
    loaded_at: float = field(default_factory=time.monotonic)

    @property
    def total_credits(self) -> int:
        return self.credits_remaining + self.credits_bonus

    @property
    def is_loaded(self) -> bool:
        return self.business_id is not None


class SessionPreloader:
    """Preloads business session data in a single efficient query."""

    def __init__(self):
        self._preload_count = 0
        self._cache_ttl = 5.0  # seconds
        self._cache: dict[str, PreloadedSession] = {}

    @property
    def preload_count(self) -> int:
        return self._preload_count

    def reset_stats(self) -> None:
        self._preload_count = 0

    def clear_cache(self) -> None:
        self._cache.clear()

    async def preload(
        self,
        session: AsyncSession,
        email: str | None = None,
        business_slug: str | None = None,
    ) -> PreloadedSession:
        """Preload session data for a user or business.

        Args:
            session: Database session.
            email: User email to look up businesses.
            business_slug: Specific business slug to preload.

        Returns:
            PreloadedSession with all commonly needed fields.
        """
        cache_key = f"{email or ''}:{business_slug or ''}"

        # Check cache
        cached = self._cache.get(cache_key)
        if cached and (time.monotonic() - cached.loaded_at) < self._cache_ttl:
            return cached

        from arclane.models.tables import Business

        preloaded = PreloadedSession(email=email)

        if business_slug:
            result = await session.execute(
                select(
                    Business.id,
                    Business.slug,
                    Business.name,
                    Business.plan,
                    Business.credits_remaining,
                    Business.credits_bonus,
                    Business.template,
                    Business.owner_email,
                ).where(Business.slug == business_slug)
            )
            row = result.first()
            if row:
                preloaded.business_id = row[0]
                preloaded.business_slug = row[1]
                preloaded.business_name = row[2]
                preloaded.plan = row[3]
                preloaded.credits_remaining = row[4]
                preloaded.credits_bonus = row[5]
                preloaded.template = row[6]
                if email is None:
                    preloaded.email = row[7]

        elif email:
            result = await session.execute(
                select(
                    Business.id,
                    Business.slug,
                    Business.name,
                    Business.plan,
                    Business.credits_remaining,
                    Business.credits_bonus,
                    Business.template,
                ).where(Business.owner_email == email)
                .where(~Business.slug.startswith("_user-"))
                .order_by(Business.created_at.desc())
                .limit(1)
            )
            row = result.first()
            if row:
                preloaded.business_id = row[0]
                preloaded.business_slug = row[1]
                preloaded.business_name = row[2]
                preloaded.plan = row[3]
                preloaded.credits_remaining = row[4]
                preloaded.credits_bonus = row[5]
                preloaded.template = row[6]

        self._preload_count += 1
        self._cache[cache_key] = preloaded
        return preloaded


# Singleton
session_preloader = SessionPreloader()
