"""Response body minification.

Item 89: Minifies JSON API responses by removing unnecessary whitespace,
null values, and empty collections. Reduces bandwidth for API consumers.
"""

import json
import re
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from arclane.core.logging import get_logger

log = get_logger("performance.minification")


class ResponseMinifier:
    """Minifies JSON response bodies."""

    def __init__(
        self,
        strip_nulls: bool = True,
        strip_empty: bool = False,
        compact_json: bool = True,
    ):
        self._strip_nulls = strip_nulls
        self._strip_empty = strip_empty
        self._compact = compact_json
        self._enabled = True
        self._bytes_saved = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def bytes_saved(self) -> int:
        return self._bytes_saved

    def reset_stats(self) -> None:
        self._bytes_saved = 0

    def minify_json(self, data: Any) -> Any:
        """Recursively minify a JSON-serializable data structure."""
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if self._strip_nulls and value is None:
                    continue
                if self._strip_empty and isinstance(value, (list, dict)) and not value:
                    continue
                result[key] = self.minify_json(value)
            return result
        elif isinstance(data, list):
            return [self.minify_json(item) for item in data]
        return data

    def minify_body(self, body: bytes, content_type: str = "") -> bytes:
        """Minify a response body if it's JSON."""
        if not self._enabled:
            return body

        if "application/json" not in content_type:
            return body

        try:
            data = json.loads(body)
            minified = self.minify_json(data)
            result = json.dumps(minified, separators=(",", ":"), ensure_ascii=False).encode()
            saved = len(body) - len(result)
            if saved > 0:
                self._bytes_saved += saved
            return result
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body

    def minify_html(self, html: str) -> str:
        """Basic HTML minification — collapse whitespace between tags."""
        if not self._enabled:
            return html
        # Collapse runs of whitespace
        result = re.sub(r">\s+<", "><", html)
        result = re.sub(r"\s{2,}", " ", result)
        return result.strip()


class MinificationMiddleware(BaseHTTPMiddleware):
    """Middleware that minifies JSON API responses."""

    def __init__(self, app: Any, minifier: ResponseMinifier | None = None):
        super().__init__(app)
        self._minifier = minifier or response_minifier

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)

        if not self._minifier.enabled:
            return response

        # Only minify API JSON responses
        if not request.url.path.startswith("/api/"):
            return response

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read and minify body
        body = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                body += chunk.encode()
            else:
                body += chunk

        minified = self._minifier.minify_body(body, content_type)

        return Response(
            content=minified,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="application/json",
        )


# Singleton
response_minifier = ResponseMinifier()
