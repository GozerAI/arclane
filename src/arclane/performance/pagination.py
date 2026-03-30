"""Response pagination with Link headers.

Item 75: Implements RFC 8288 Link header pagination for list endpoints,
providing first/prev/next/last navigation links.
"""

from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

from fastapi import Request, Response

from arclane.core.logging import get_logger

log = get_logger("performance.pagination")


class PaginationParams:
    """Parsed pagination parameters from a request."""

    def __init__(self, page: int = 1, per_page: int = 50, max_per_page: int = 200):
        self.page = max(1, page)
        self.per_page = min(max(1, per_page), max_per_page)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        return self.per_page


class PaginatedResponse:
    """Wraps a list response with pagination metadata and Link headers."""

    def __init__(
        self,
        items: list[Any],
        total: int,
        params: PaginationParams,
        request: Request,
    ):
        self.items = items
        self.total = total
        self.page = params.page
        self.per_page = params.per_page
        self.total_pages = max(1, (total + params.per_page - 1) // params.per_page)
        self._base_url = str(request.url).split("?")[0]
        self._original_query = dict(request.query_params)

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    def _build_url(self, page: int) -> str:
        """Build a URL for a specific page."""
        params = dict(self._original_query)
        params["page"] = str(page)
        params["per_page"] = str(self.per_page)
        return f"{self._base_url}?{urlencode(params)}"

    def link_header(self) -> str:
        """Generate RFC 8288 Link header value."""
        links = []
        links.append(f'<{self._build_url(1)}>; rel="first"')
        links.append(f'<{self._build_url(self.total_pages)}>; rel="last"')

        if self.has_prev:
            links.append(f'<{self._build_url(self.page - 1)}>; rel="prev"')
        if self.has_next:
            links.append(f'<{self._build_url(self.page + 1)}>; rel="next"')

        return ", ".join(links)

    def apply_headers(self, response: Response) -> Response:
        """Apply pagination headers to a FastAPI response."""
        response.headers["Link"] = self.link_header()
        response.headers["X-Total-Count"] = str(self.total)
        response.headers["X-Page"] = str(self.page)
        response.headers["X-Per-Page"] = str(self.per_page)
        response.headers["X-Total-Pages"] = str(self.total_pages)
        return response

    def to_dict(self) -> dict:
        """Return pagination metadata as a dict (for JSON body inclusion)."""
        return {
            "items": self.items,
            "pagination": {
                "page": self.page,
                "per_page": self.per_page,
                "total": self.total,
                "total_pages": self.total_pages,
                "has_next": self.has_next,
                "has_prev": self.has_prev,
            },
        }


def paginate(
    items: list[Any],
    total: int,
    request: Request,
    response: Response,
    page: int = 1,
    per_page: int = 50,
) -> list[Any]:
    """Convenience function to apply Link headers and return items.

    Usage in a route:
        return paginate(items, total, request, response, page, per_page)
    """
    params = PaginationParams(page=page, per_page=per_page)
    paginated = PaginatedResponse(items, total, params, request)
    paginated.apply_headers(response)
    return items
