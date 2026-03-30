"""API response time budgets per endpoint.

Item 83: Defines and enforces time budgets for each endpoint category.
Logs warnings when endpoints exceed their budget and adds timing headers.
"""

import time
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from arclane.core.logging import get_logger

log = get_logger("performance.time_budgets")

# Time budgets in milliseconds by endpoint pattern
DEFAULT_BUDGETS: dict[str, float] = {
    "/health": 50,
    "/health/detailed": 5000,
    "/api/auth/": 2000,
    "/api/businesses": 500,
    "/api/live": 500,
    "/api/live/stats": 1000,
    "/api/live/stream": 30000,
    "/api/workflows": 1000,
}

# Default budget for unmatched API endpoints
DEFAULT_API_BUDGET_MS = 3000

# Budget for cycle-related endpoints (long-running operations)
CYCLE_BUDGET_MS = 10000


class TimeBudgetRegistry:
    """Registry of per-endpoint time budgets."""

    def __init__(self):
        self._budgets: dict[str, float] = dict(DEFAULT_BUDGETS)
        self._violations: list[dict] = []
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def violations(self) -> list[dict]:
        return list(self._violations)

    def clear_violations(self) -> None:
        self._violations.clear()

    def set_budget(self, pattern: str, budget_ms: float) -> None:
        """Set a time budget for an endpoint pattern."""
        self._budgets[pattern] = budget_ms

    def get_budget(self, path: str) -> float:
        """Get the time budget for a given path in milliseconds."""
        # Check exact match first
        if path in self._budgets:
            return self._budgets[path]

        # Check prefix matches (longest prefix wins)
        best_match = ""
        best_budget = DEFAULT_API_BUDGET_MS

        for pattern, budget in self._budgets.items():
            if path.startswith(pattern) and len(pattern) > len(best_match):
                best_match = pattern
                best_budget = budget

        # Special handling for cycle endpoints
        if "/cycles" in path:
            return CYCLE_BUDGET_MS

        return best_budget if best_match else DEFAULT_API_BUDGET_MS

    def check_budget(self, path: str, elapsed_ms: float) -> bool:
        """Check if a request was within budget. Returns True if OK."""
        if not self._enabled:
            return True

        budget = self.get_budget(path)
        if elapsed_ms > budget:
            violation = {
                "path": path,
                "elapsed_ms": round(elapsed_ms, 1),
                "budget_ms": budget,
                "overage_ms": round(elapsed_ms - budget, 1),
                "overage_pct": round((elapsed_ms - budget) / budget * 100, 1),
            }
            self._violations.append(violation)
            # Keep last 1000 violations
            if len(self._violations) > 1000:
                self._violations = self._violations[-500:]
            return False
        return True

    def stats(self) -> dict:
        """Return budget violation statistics."""
        if not self._violations:
            return {"total_violations": 0, "worst_offenders": []}

        # Group by path
        by_path: dict[str, list] = {}
        for v in self._violations:
            by_path.setdefault(v["path"], []).append(v)

        worst = sorted(
            [
                {
                    "path": path,
                    "count": len(violations),
                    "avg_overage_ms": round(
                        sum(v["overage_ms"] for v in violations) / len(violations), 1
                    ),
                    "max_overage_ms": max(v["overage_ms"] for v in violations),
                }
                for path, violations in by_path.items()
            ],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        return {
            "total_violations": len(self._violations),
            "worst_offenders": worst,
        }


class TimeBudgetMiddleware(BaseHTTPMiddleware):
    """Middleware that tracks response times against endpoint budgets."""

    def __init__(self, app: Any, registry: TimeBudgetRegistry | None = None):
        super().__init__(app)
        self._registry = registry or time_budget_registry

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if not request.url.path.startswith("/api/") and request.url.path not in ("/health", "/health/detailed"):
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        budget = self._registry.get_budget(request.url.path)
        within_budget = self._registry.check_budget(request.url.path, elapsed_ms)

        response.headers["X-Response-Time-Ms"] = str(round(elapsed_ms, 1))
        response.headers["X-Time-Budget-Ms"] = str(round(budget, 1))

        if not within_budget:
            response.headers["X-Budget-Exceeded"] = "true"
            log.warning(
                "Time budget exceeded: %s took %.0fms (budget: %.0fms)",
                request.url.path, elapsed_ms, budget,
            )

        return response


# Singleton
time_budget_registry = TimeBudgetRegistry()
