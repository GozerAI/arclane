"""Business configuration cache.

Item 65: In-memory cache for business configuration (agent_config, plan,
template) to avoid repeated database lookups during request processing.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from arclane.core.logging import get_logger

log = get_logger("performance.business_cache")


@dataclass
class CachedBusinessConfig:
    """Cached business configuration."""
    business_id: int
    slug: str
    name: str
    plan: str
    template: str | None
    agent_config: dict | None
    credits_remaining: int
    credits_bonus: int
    cached_at: float = field(default_factory=time.monotonic)

    @property
    def total_credits(self) -> int:
        return self.credits_remaining + self.credits_bonus


class BusinessConfigCache:
    """TTL-based cache for business configurations.

    Avoids repeated database lookups for the same business within a
    configurable window. Writes (credit changes, plan upgrades) invalidate
    the cache entry for that business.
    """

    def __init__(self, ttl_s: float = 30.0, max_size: int = 512):
        self._cache: dict[str, CachedBusinessConfig] = {}
        self._ttl = ttl_s
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "ttl_s": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(
                self._hits / max(self._hits + self._misses, 1), 4
            ),
        }

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0

    def get(self, slug: str) -> CachedBusinessConfig | None:
        """Get cached config for a business, or None if expired/missing."""
        entry = self._cache.get(slug)
        if entry is None:
            self._misses += 1
            return None

        if time.monotonic() - entry.cached_at > self._ttl:
            del self._cache[slug]
            self._misses += 1
            return None

        self._hits += 1
        return entry

    def put(self, business: Any) -> CachedBusinessConfig:
        """Cache a business's configuration.

        Args:
            business: A Business ORM model instance.

        Returns:
            The cached config object.
        """
        if len(self._cache) >= self._max_size:
            self._evict_oldest()

        config = CachedBusinessConfig(
            business_id=business.id,
            slug=business.slug,
            name=business.name,
            plan=business.plan,
            template=business.template,
            agent_config=business.agent_config,
            credits_remaining=business.credits_remaining,
            credits_bonus=business.credits_bonus,
        )
        self._cache[business.slug] = config
        return config

    def invalidate(self, slug: str) -> bool:
        """Invalidate a cached business config.

        Returns True if an entry was removed.
        """
        removed = self._cache.pop(slug, None)
        if removed:
            log.debug("Cache invalidated for business %s", slug)
        return removed is not None

    def invalidate_all(self) -> int:
        """Clear all cached configs. Returns count of removed entries."""
        count = len(self._cache)
        self._cache.clear()
        return count

    def _evict_oldest(self) -> None:
        """Evict the oldest cache entry."""
        if not self._cache:
            return
        oldest_key = min(
            self._cache.keys(),
            key=lambda k: self._cache[k].cached_at,
        )
        del self._cache[oldest_key]


# Singleton
business_config_cache = BusinessConfigCache()
