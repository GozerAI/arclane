"""CDN caching headers for static assets and CDN integration.

Item 49: Adds proper Cache-Control, ETag, and CDN-related headers.
Item 215: CDN integration for static asset delivery with origin pull support.
"""

import hashlib
import time
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from arclane.core.logging import get_logger

log = get_logger("performance.cdn")

# Static asset extensions and their cache durations (seconds)
STATIC_CACHE_RULES = {
    ".js": 86400 * 30,    # 30 days
    ".css": 86400 * 30,   # 30 days
    ".png": 86400 * 365,  # 1 year
    ".jpg": 86400 * 365,
    ".jpeg": 86400 * 365,
    ".gif": 86400 * 365,
    ".svg": 86400 * 365,
    ".ico": 86400 * 365,
    ".woff2": 86400 * 365,
    ".woff": 86400 * 365,
    ".ttf": 86400 * 365,
    ".webp": 86400 * 365,
}

# API response cache durations
API_CACHE_RULES = {
    "/health": 10,
    "/api/live": 5,
    "/api/live/stats": 30,
    "/robots.txt": 86400,
}


class CDNCacheConfig:
    """Configuration for CDN integration."""

    def __init__(
        self,
        cdn_base_url: str = "",
        origin_shield: bool = False,
        vary_headers: list[str] | None = None,
    ):
        self.cdn_base_url = cdn_base_url
        self.origin_shield = origin_shield
        self.vary_headers = vary_headers or ["Accept", "Accept-Encoding"]
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self.cdn_base_url)

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def rewrite_url(self, path: str) -> str:
        """Rewrite a static asset path to use the CDN base URL."""
        if not self.enabled:
            return path
        return f"{self.cdn_base_url.rstrip('/')}{path}"


def compute_etag(content: bytes | str) -> str:
    """Compute a weak ETag from content."""
    if isinstance(content, str):
        content = content.encode()
    return f'W/"{hashlib.md5(content).hexdigest()[:16]}"'


def get_cache_duration(path: str) -> int | None:
    """Determine cache duration for a given request path."""
    # Check static rules by extension
    for ext, duration in STATIC_CACHE_RULES.items():
        if path.endswith(ext):
            return duration

    # Check API rules
    for prefix, duration in API_CACHE_RULES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return duration

    return None


class CDNCacheMiddleware(BaseHTTPMiddleware):
    """Middleware that adds Cache-Control and CDN headers to responses."""

    def __init__(self, app: Any, cdn_config: CDNCacheConfig | None = None):
        super().__init__(app)
        self._cdn_config = cdn_config or CDNCacheConfig()

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        path = request.url.path

        duration = get_cache_duration(path)
        if duration is not None:
            if path.startswith("/static/") or not path.startswith("/api/"):
                # Immutable static assets
                response.headers["Cache-Control"] = (
                    f"public, max-age={duration}, immutable"
                )
            else:
                # API responses — short cache, must-revalidate
                response.headers["Cache-Control"] = (
                    f"public, max-age={duration}, must-revalidate"
                )

            # Vary header for correct CDN caching
            response.headers["Vary"] = ", ".join(
                self._cdn_config.vary_headers
                if self._cdn_config
                else ["Accept", "Accept-Encoding"]
            )
        else:
            # Default: no-cache for API endpoints
            if path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

        # CDN-specific headers
        if self._cdn_config and self._cdn_config.enabled:
            response.headers["X-CDN-Origin"] = "arclane"
            if self._cdn_config.origin_shield:
                response.headers["X-Origin-Shield"] = "true"

        return response


# Default singleton config
cdn_config = CDNCacheConfig()
