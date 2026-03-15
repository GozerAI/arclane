"""Request prioritization for critical paths.

Item 208: Classifies incoming requests by priority level and schedules
high-priority requests (auth, billing, cycles) ahead of lower-priority ones.
"""

import asyncio
import time
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Any

from arclane.core.logging import get_logger

log = get_logger("performance.request_priority")


class Priority(IntEnum):
    """Request priority levels. Lower number = higher priority."""
    CRITICAL = 0   # Auth, health checks
    HIGH = 1       # Billing, cycles
    NORMAL = 2     # Business CRUD, settings
    LOW = 3        # Feed, live, metrics
    BACKGROUND = 4  # Stats, workflows


# Path prefix to priority mapping
PRIORITY_RULES: dict[str, Priority] = {
    "/health": Priority.CRITICAL,
    "/api/auth/": Priority.CRITICAL,
    "/api/businesses/{slug}/billing": Priority.HIGH,
    "/api/businesses/{slug}/cycles": Priority.HIGH,
    "/api/businesses": Priority.NORMAL,
    "/api/businesses/{slug}/settings": Priority.NORMAL,
    "/api/businesses/{slug}/feed": Priority.LOW,
    "/api/businesses/{slug}/content": Priority.LOW,
    "/api/businesses/{slug}/metrics": Priority.LOW,
    "/api/live": Priority.LOW,
    "/api/live/stats": Priority.BACKGROUND,
    "/api/workflows": Priority.BACKGROUND,
}


@dataclass
class PrioritizedRequest:
    """A request with an assigned priority."""
    priority: Priority
    path: str
    method: str
    queued_at: float = field(default_factory=time.monotonic)

    @property
    def wait_time_ms(self) -> float:
        return (time.monotonic() - self.queued_at) * 1000


class RequestPrioritizer:
    """Classifies and tracks request priorities.

    Uses semaphores per priority level to limit concurrency for
    lower-priority requests when the system is under load.
    """

    def __init__(
        self,
        max_concurrent_critical: int = 100,
        max_concurrent_high: int = 50,
        max_concurrent_normal: int = 30,
        max_concurrent_low: int = 20,
        max_concurrent_background: int = 10,
    ):
        self._semaphores: dict[Priority, asyncio.Semaphore] = {
            Priority.CRITICAL: asyncio.Semaphore(max_concurrent_critical),
            Priority.HIGH: asyncio.Semaphore(max_concurrent_high),
            Priority.NORMAL: asyncio.Semaphore(max_concurrent_normal),
            Priority.LOW: asyncio.Semaphore(max_concurrent_low),
            Priority.BACKGROUND: asyncio.Semaphore(max_concurrent_background),
        }
        self._counts: dict[Priority, int] = {p: 0 for p in Priority}
        self._total_processed = 0

    def classify(self, method: str, path: str) -> Priority:
        """Determine the priority of a request based on its path."""
        # Direct match check
        for pattern, priority in PRIORITY_RULES.items():
            if "{slug}" in pattern:
                # For slug patterns, extract the suffix after {slug} and match
                suffix = pattern.split("{slug}")[-1]
                if path.startswith("/api/businesses/") and suffix:
                    # Split: ['', 'api', 'businesses', '<slug>', rest...]
                    parts = path.split("/", 4)
                    if len(parts) >= 5:
                        remaining = "/" + parts[4]
                        if remaining == suffix or remaining.startswith(suffix + "/"):
                            return priority
            else:
                if path == pattern or path.startswith(pattern):
                    return priority

        # Default
        if path.startswith("/api/"):
            return Priority.NORMAL
        return Priority.LOW

    async def acquire(self, priority: Priority) -> None:
        """Acquire a concurrency slot for a given priority level."""
        await self._semaphores[priority].acquire()
        self._counts[priority] += 1
        self._total_processed += 1

    def release(self, priority: Priority) -> None:
        """Release a concurrency slot."""
        self._semaphores[priority].release()
        self._counts[priority] = max(0, self._counts[priority] - 1)

    @property
    def stats(self) -> dict:
        return {
            "active": {p.name: self._counts[p] for p in Priority},
            "total_processed": self._total_processed,
        }

    def reset_stats(self) -> None:
        self._total_processed = 0


# Singleton
request_prioritizer = RequestPrioritizer()
