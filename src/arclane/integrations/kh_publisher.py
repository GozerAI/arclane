"""Knowledge Harvester publisher — sends cycle content as KH artifacts."""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from arclane.core.config import settings

# Optional resilience
try:
    from gozerai_telemetry.resilience import (
        resilient_request,
        get_circuit_breaker,
        CONSERVATIVE_RETRY,
    )
    _HAS_RESILIENCE = True
    _kh_cb = get_circuit_breaker("kh", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _kh_cb = None

log = logging.getLogger("arclane.integrations.kh_publisher")

KH_TIMEOUT = 15.0

# Map Arclane content types to KH artifact types
CONTENT_TYPE_MAP = {
    "blog": "guide",
    "social": "snippet",
    "newsletter": "guide",
    "changelog": "snippet",
    "report": "guide",
}


class KHPublisher:
    """Publishes Arclane cycle content as Knowledge Harvester artifacts."""

    def __init__(self, base_url: Optional[str] = None):
        self._base_url = base_url or settings.kh_base_url

    async def publish_content(
        self,
        business_name: str,
        content_type: str,
        title: str,
        body: str,
        category: str = "business",
    ) -> Optional[dict]:
        """Publish a content item to KH as an artifact.

        Returns the KH response dict on success, None on failure.
        Graceful degradation: never raises.
        """
        artifact_type = CONTENT_TYPE_MAP.get(content_type, "snippet")

        payload = {
            "title": title or f"{business_name} - {content_type}",
            "content": body[:5000],  # cap content size
            "artifact_type": artifact_type,
            "primary_category": category,
            "source": f"arclane:{business_name}",
            "metadata": {
                "origin": "arclane",
                "business": business_name,
                "content_type": content_type,
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        try:
            if _HAS_RESILIENCE:
                result = await resilient_request(
                    "POST", f"{self._base_url}/api/artifacts",
                    json_body=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=KH_TIMEOUT, retry_policy=CONSERVATIVE_RETRY, circuit_breaker=_kh_cb,
                )
            else:
                async with httpx.AsyncClient(timeout=KH_TIMEOUT) as client:
                    resp = await client.post(
                        f"{self._base_url}/api/artifacts",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    result = resp.json()
            if result is not None:
                log.info(
                    "Published '%s' to KH as artifact %s",
                    title or content_type, result.get("id", "unknown"),
                )
            return result
        except Exception as exc:
            log.warning("Failed to publish to KH: %s", exc)
            return None

    async def publish_cycle_results(
        self,
        business_name: str,
        results: list[dict],
    ) -> list[dict]:
        """Publish all content-bearing results from a cycle.

        Returns list of successfully published artifact references.
        """
        published = []
        for result in results:
            content_type = result.get("content_type")
            content_body = result.get("content_body")
            if not content_type or not content_body:
                continue

            artifact = await self.publish_content(
                business_name=business_name,
                content_type=content_type,
                title=result.get("content_title", ""),
                body=content_body,
                category=result.get("area", "business"),
            )
            if artifact:
                published.append({
                    "artifact_id": artifact.get("id"),
                    "content_type": content_type,
                    "title": result.get("content_title", ""),
                })
        return published
