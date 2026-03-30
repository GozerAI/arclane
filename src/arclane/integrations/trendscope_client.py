"""Trendscope client for Arclane — fetches market signals for cycle enrichment."""

import logging
from typing import Optional

import httpx

from arclane.core.config import settings

# Optional resilience
try:
    from gozerai_telemetry.resilience import (
        resilient_request,
        get_circuit_breaker,
        DEFAULT_RETRY,
    )
    _HAS_RESILIENCE = True
    _ts_cb = get_circuit_breaker("trendscope", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _ts_cb = None

log = logging.getLogger("arclane.integrations.trendscope")

TS_TIMEOUT = 10.0


class TrendscopeClient:
    """Fetches trend signals from Trendscope to enrich business cycles."""

    def __init__(self, base_url: Optional[str] = None, bearer_token: Optional[str] = None):
        self._base_url = base_url or settings.trendscope_base_url
        self._token = bearer_token or ""

    async def get_relevant_signals(self, business_description: str, limit: int = 5) -> list[dict]:
        """Fetch top trends and filter for relevance to the business.

        Returns list of {name, score, velocity, category, status} dicts.
        Graceful degradation: returns [] on any failure.
        """
        try:
            headers = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            if _HAS_RESILIENCE:
                url = f"{self._base_url}/v1/trends/top?limit={limit}"
                trends = await resilient_request(
                    "GET", url, headers=headers,
                    timeout=TS_TIMEOUT, retry_policy=DEFAULT_RETRY, circuit_breaker=_ts_cb,
                )
            else:
                async with httpx.AsyncClient(timeout=TS_TIMEOUT) as client:
                    resp = await client.get(
                        f"{self._base_url}/v1/trends/top",
                        params={"limit": limit},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    trends = resp.json()

            if not isinstance(trends, list):
                return []

            return [
                {
                    "name": t.get("name", ""),
                    "score": t.get("score", 0),
                    "velocity": t.get("velocity", 0),
                    "category": t.get("category", ""),
                    "status": t.get("status", ""),
                }
                for t in trends[:limit]
            ]
        except Exception as exc:
            log.warning("Failed to fetch Trendscope signals: %s", exc)
            return []

    async def get_strong_buy_signals(self, min_score: int = 70) -> list[dict]:
        """Fetch strong-buy signals from Trendscope.

        Returns list of signal dicts. Graceful degradation.
        """
        try:
            headers = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            if _HAS_RESILIENCE:
                url = f"{self._base_url}/v1/signals/strong-buy?min_score={min_score}"
                data = await resilient_request(
                    "GET", url, headers=headers,
                    timeout=TS_TIMEOUT, retry_policy=DEFAULT_RETRY, circuit_breaker=_ts_cb,
                )
                if data is None:
                    return []
                return data.get("trends", [])
            else:
                async with httpx.AsyncClient(timeout=TS_TIMEOUT) as client:
                    resp = await client.get(
                        f"{self._base_url}/v1/signals/strong-buy",
                        params={"min_score": min_score},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("trends", [])
        except Exception as exc:
            log.warning("Failed to fetch strong-buy signals: %s", exc)
            return []

    def format_signal_context(self, signals: list[dict]) -> str:
        """Format signals into a context string for task descriptions."""
        if not signals:
            return ""
        lines = ["Current market intelligence from Trendscope:"]
        for s in signals[:5]:
            name = s.get("name", "Unknown")
            score = s.get("score", 0)
            velocity = s.get("velocity", 0)
            category = s.get("category", "")
            direction = "rising" if velocity > 0 else "falling" if velocity < 0 else "stable"
            lines.append(f"  - {name} ({category}): score {score}, {direction}")
        return "\n".join(lines)
