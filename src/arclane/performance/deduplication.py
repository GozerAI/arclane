"""Query deduplication for concurrent identical requests.

Item 15: When multiple clients request the same resource simultaneously,
only one actual query/computation runs. Others await the same result.
"""

import asyncio
import hashlib
import time
from typing import Any, Callable, Coroutine

from arclane.core.logging import get_logger

log = get_logger("performance.deduplication")


class RequestDeduplicator:
    """Deduplicates concurrent identical async requests.

    Uses a request key (derived from method + path + query params) to detect
    duplicates. The first request executes; concurrent duplicates await the
    same future and receive the same result.
    """

    def __init__(self, ttl_s: float = 1.0):
        self._inflight: dict[str, asyncio.Future] = {}
        self._ttl = ttl_s
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "inflight": len(self._inflight),
        }

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(method: str, path: str, query: str = "", body_hash: str = "") -> str:
        """Create a deduplication key from request attributes."""
        raw = f"{method}:{path}:{query}:{body_hash}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def deduplicate(
        self,
        key: str,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    ) -> Any:
        """Execute coro_factory() or return an inflight result for the same key.

        Args:
            key: Deduplication key.
            coro_factory: Zero-arg callable that returns a coroutine.

        Returns:
            The result from the coroutine.
        """
        if key in self._inflight:
            future = self._inflight[key]
            if not future.done():
                self._hits += 1
                log.debug("Dedup hit for key %s", key[:8])
                return await asyncio.shield(future)

        self._misses += 1
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._inflight[key] = future

        try:
            result = await coro_factory()
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            # Schedule cleanup after TTL
            asyncio.get_event_loop().call_later(
                self._ttl, self._cleanup_key, key
            )

    def _cleanup_key(self, key: str) -> None:
        self._inflight.pop(key, None)


# Singleton
request_deduplicator = RequestDeduplicator()
