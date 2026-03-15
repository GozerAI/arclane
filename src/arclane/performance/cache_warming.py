"""Response cache warming for popular endpoints.

Item 96: Pre-fetches and caches responses for high-traffic endpoints
(live feed, stats, popular business dashboards) on a schedule.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from arclane.core.logging import get_logger

log = get_logger("performance.cache_warming")


@dataclass
class WarmCacheEntry:
    """A pre-warmed cache entry."""
    key: str
    data: Any
    warmed_at: float = field(default_factory=time.monotonic)
    ttl_s: float = 60.0
    hit_count: int = 0

    @property
    def is_expired(self) -> bool:
        return time.monotonic() - self.warmed_at > self.ttl_s


@dataclass
class WarmTarget:
    """Definition of an endpoint to warm."""
    key: str
    fetcher: Callable[[], Coroutine[Any, Any, Any]]
    ttl_s: float = 60.0
    priority: int = 0  # Higher = more important


class CacheWarmer:
    """Proactively warms caches for popular endpoints.

    Runs fetchers on a schedule and stores results for instant retrieval.
    Endpoints register themselves as warm targets with a fetcher function
    and TTL.
    """

    def __init__(self):
        self._targets: dict[str, WarmTarget] = {}
        self._cache: dict[str, WarmCacheEntry] = {}
        self._warm_count = 0
        self._running = False

    @property
    def stats(self) -> dict:
        active = sum(1 for e in self._cache.values() if not e.is_expired)
        return {
            "targets": len(self._targets),
            "cached": len(self._cache),
            "active": active,
            "warm_cycles": self._warm_count,
            "total_hits": sum(e.hit_count for e in self._cache.values()),
        }

    def register(
        self,
        key: str,
        fetcher: Callable[[], Coroutine[Any, Any, Any]],
        ttl_s: float = 60.0,
        priority: int = 0,
    ) -> None:
        """Register a warm target."""
        self._targets[key] = WarmTarget(
            key=key, fetcher=fetcher, ttl_s=ttl_s, priority=priority,
        )
        log.debug("Registered warm target: %s (ttl=%ds)", key, ttl_s)

    def unregister(self, key: str) -> None:
        """Remove a warm target."""
        self._targets.pop(key, None)
        self._cache.pop(key, None)

    def get(self, key: str) -> Any | None:
        """Retrieve a warmed cache entry, or None if expired/missing."""
        entry = self._cache.get(key)
        if entry is None or entry.is_expired:
            return None
        entry.hit_count += 1
        return entry.data

    async def warm_one(self, key: str) -> bool:
        """Warm a single target by running its fetcher."""
        target = self._targets.get(key)
        if target is None:
            return False

        try:
            data = await target.fetcher()
            self._cache[key] = WarmCacheEntry(
                key=key, data=data, ttl_s=target.ttl_s,
            )
            return True
        except Exception as exc:
            log.warning("Failed to warm cache for %s: %s", key, exc)
            return False

    async def warm_all(self) -> dict[str, bool]:
        """Warm all registered targets, ordered by priority."""
        targets = sorted(
            self._targets.values(),
            key=lambda t: t.priority,
            reverse=True,
        )
        results = {}
        for target in targets:
            results[target.key] = await self.warm_one(target.key)
        self._warm_count += 1
        log.info(
            "Cache warm cycle %d: %d/%d succeeded",
            self._warm_count,
            sum(results.values()),
            len(results),
        )
        return results

    async def run_periodic(self, interval_s: float = 30.0) -> None:
        """Run warm cycles periodically until stopped."""
        self._running = True
        log.info("Cache warmer started (interval=%ds)", interval_s)
        while self._running:
            await self.warm_all()
            await asyncio.sleep(interval_s)

    def stop(self) -> None:
        """Stop the periodic warm loop."""
        self._running = False

    def clear(self) -> None:
        """Clear all cached data."""
        self._cache.clear()


# Singleton
cache_warmer = CacheWarmer()
