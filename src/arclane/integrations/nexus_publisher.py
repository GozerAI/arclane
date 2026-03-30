"""Nexus publisher — sends cycle analysis results as knowledge items."""

import json
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
    _nexus_cb = get_circuit_breaker("nexus", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _nexus_cb = None

log = logging.getLogger("arclane.integrations.nexus")

NEXUS_TIMEOUT = 10.0


class NexusPublisher:
    """Publishes Arclane cycle insights to Nexus as knowledge items."""

    def __init__(self, base_url: Optional[str] = None):
        self._base_url = base_url or settings.nexus_base_url

    async def publish_cycle_insights(
        self,
        business_name: str,
        business_description: str,
        cycle_results: list[dict],
    ) -> list[dict]:
        """Extract and publish actionable insights from cycle results.

        Focuses on strategy and market_research results which contain
        the most knowledge-worthy analysis.

        Returns list of published knowledge references.
        Graceful degradation: never raises.
        """
        published = []
        knowledge_areas = {"strategy", "market_research", "finance", "operations"}

        for result in cycle_results:
            area = result.get("area", "")
            status = result.get("status", "")
            analysis = result.get("result", "")

            if area not in knowledge_areas or status != "completed" or not analysis:
                continue

            # Truncate to reasonable size
            content = analysis[:3000]

            payload = {
                "content": f"Business analysis for '{business_name}' ({area}): {content}",
                "knowledge_type": "FACTUAL",
                "source": f"arclane:cycle:{business_name}",
                "confidence": 0.7,
                "context_tags": ["arclane", "business_analysis", area, business_name.lower().replace(" ", "_")],
            }

            try:
                if _HAS_RESILIENCE:
                    result_data = await resilient_request(
                        "POST", f"{self._base_url}/api/knowledge",
                        json_body=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=NEXUS_TIMEOUT, retry_policy=DEFAULT_RETRY, circuit_breaker=_nexus_cb,
                    )
                else:
                    async with httpx.AsyncClient(timeout=NEXUS_TIMEOUT) as client:
                        resp = await client.post(
                            f"{self._base_url}/api/knowledge",
                            json=payload,
                            headers={"Content-Type": "application/json"},
                        )
                        resp.raise_for_status()
                        result_data = resp.json()
                if result_data is not None:
                    published.append({
                        "knowledge_id": result_data.get("id", ""),
                        "area": area,
                        "source": payload["source"],
                    })
                    log.info("Published %s insight to Nexus for %s", area, business_name)
            except Exception as exc:
                log.warning("Failed to publish to Nexus: %s", exc)

        return published

    async def get_relevant_knowledge(
        self,
        business_description: str,
        limit: int = 5,
    ) -> list[dict]:
        """Fetch knowledge items relevant to a business description.

        Returns list of {content, source, confidence} dicts.
        Graceful degradation: returns [] on failure.
        """
        try:
            if _HAS_RESILIENCE:
                q = business_description[:200]
                url = f"{self._base_url}/api/knowledge/search?q={q}&limit={limit}"
                data = await resilient_request(
                    "GET", url,
                    headers={"Accept": "application/json"},
                    timeout=NEXUS_TIMEOUT, retry_policy=DEFAULT_RETRY, circuit_breaker=_nexus_cb,
                )
                if data is None:
                    return []
            else:
                async with httpx.AsyncClient(timeout=NEXUS_TIMEOUT) as client:
                    resp = await client.get(
                        f"{self._base_url}/api/knowledge/search",
                        params={"q": business_description[:200], "limit": limit},
                        headers={"Accept": "application/json"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
            items = data if isinstance(data, list) else data.get("items", [])
            return [
                {
                    "content": item.get("content", "")[:500],
                    "source": item.get("source", ""),
                    "confidence": item.get("confidence", 0),
                }
                for item in items[:limit]
            ]
        except Exception as exc:
            log.warning("Failed to fetch Nexus knowledge: %s", exc)
            return []

    def format_knowledge_context(self, items: list[dict]) -> str:
        """Format knowledge items into context for task descriptions."""
        if not items:
            return ""
        lines = ["Relevant intelligence from Nexus knowledge base:"]
        for item in items[:5]:
            content = item.get("content", "")[:200]
            source = item.get("source", "unknown")
            lines.append(f"  - [{source}] {content}")
        return "\n".join(lines)
