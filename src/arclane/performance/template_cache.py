"""Template rendering cache with versioned keys.

Item 41: Caches rendered template output with version-tagged keys so that
template updates automatically invalidate stale entries.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from arclane.core.logging import get_logger

log = get_logger("performance.template_cache")


@dataclass
class CacheEntry:
    """A single cached rendered template."""
    content: str
    version: str
    created_at: float = field(default_factory=time.monotonic)
    access_count: int = 0


class TemplateRenderCache:
    """LRU cache for rendered templates, keyed by template name + version.

    Version is derived from template content hash, so any change to the
    source template automatically invalidates the cache entry.
    """

    def __init__(self, max_size: int = 256, ttl_s: float = 3600.0):
        self._cache: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._ttl = ttl_s
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(
                self._hits / max(self._hits + self._misses, 1), 4
            ),
        }

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0

    @staticmethod
    def version_key(template_name: str, template_source: str, context_hash: str = "") -> tuple[str, str]:
        """Generate a cache key and version from template name, source, and context.

        Returns:
            Tuple of (cache_key, version_hash).
        """
        version = hashlib.sha256(template_source.encode()).hexdigest()[:16]
        cache_key = f"{template_name}:{version}:{context_hash}"
        return cache_key, version

    def get(self, key: str, version: str) -> str | None:
        """Retrieve a cached rendering if it matches the version and TTL."""
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        # Version mismatch means template source changed
        if entry.version != version:
            self._misses += 1
            del self._cache[key]
            return None

        # TTL check
        if time.monotonic() - entry.created_at > self._ttl:
            self._misses += 1
            del self._cache[key]
            return None

        self._hits += 1
        entry.access_count += 1
        return entry.content

    def put(self, key: str, version: str, content: str) -> None:
        """Store a rendered template in the cache."""
        # Evict LRU if at capacity
        if len(self._cache) >= self._max_size and key not in self._cache:
            self._evict_lru()

        self._cache[key] = CacheEntry(
            content=content,
            version=version,
        )

    def invalidate(self, template_name: str) -> int:
        """Invalidate all cache entries for a given template name.

        Returns the number of entries removed.
        """
        to_remove = [k for k in self._cache if k.startswith(f"{template_name}:")]
        for k in to_remove:
            del self._cache[k]
        if to_remove:
            log.info("Invalidated %d cache entries for template %s", len(to_remove), template_name)
        return len(to_remove)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def _evict_lru(self) -> None:
        """Evict the least recently used entry."""
        if not self._cache:
            return
        # Find entry with oldest created_at and lowest access_count
        lru_key = min(
            self._cache.keys(),
            key=lambda k: (self._cache[k].access_count, self._cache[k].created_at),
        )
        del self._cache[lru_key]

    def render_cached(
        self,
        template_name: str,
        template_source: str,
        render_fn: Any,
        context: dict | None = None,
    ) -> str:
        """Render a template, using cache if available.

        Args:
            template_name: Template identifier.
            template_source: Raw template source (used for versioning).
            render_fn: Callable(template_source, context) -> str.
            context: Template context variables.

        Returns:
            Rendered template string.
        """
        ctx_hash = hashlib.sha256(
            str(sorted((context or {}).items())).encode()
        ).hexdigest()[:16]

        key, version = self.version_key(template_name, template_source, ctx_hash)
        cached = self.get(key, version)
        if cached is not None:
            return cached

        rendered = render_fn(template_source, context or {})
        self.put(key, version, rendered)
        return rendered


# Singleton
template_cache = TemplateRenderCache()
