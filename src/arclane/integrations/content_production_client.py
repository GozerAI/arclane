"""Content Production client — distributes content to marketplaces via CP service."""

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Optional

# Optional resilience
try:
    from gozerai_telemetry.resilience import (
        resilient_fetch,
        get_circuit_breaker,
        DEFAULT_RETRY,
    )
    _HAS_RESILIENCE = True
    _cp_cb = get_circuit_breaker("content_production", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _cp_cb = None

log = logging.getLogger("arclane.integrations.content_production")

CP_TIMEOUT = 15.0
MARKETPLACE_PLATFORMS = {"gumroad", "etsy", "shopify", "amazon-kdp"}

_DEFAULT_CP_URL = "http://localhost:8013"


class ContentProductionClient:
    """Sends content to the Content Production service for marketplace distribution."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        service_token: Optional[str] = None,
    ):
        self._base_url = (
            base_url
            or os.environ.get("CONTENT_PRODUCTION_URL", _DEFAULT_CP_URL)
        )
        self._token = service_token or os.environ.get("ARCLANE_CP_SERVICE_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._token:
            headers["X-Service-Token"] = self._token
            headers["X-Service-Name"] = "arclane"
        return headers

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """Perform a synchronous HTTP request via urllib.

        Returns parsed JSON on success, None on failure (graceful degradation).
        """
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None

        try:
            if _HAS_RESILIENCE:
                result = resilient_fetch(
                    url,
                    method=method,
                    headers=self._headers(),
                    data=data,
                    timeout=CP_TIMEOUT,
                    retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_cp_cb,
                )
                return result
            else:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers=self._headers(),
                    method=method,
                )
                with urllib.request.urlopen(req, timeout=int(CP_TIMEOUT)) as resp:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log.warning("Content Production %s %s failed: %s", method, path, exc)
            return None

    def distribute_content(
        self,
        title: str,
        description: str,
        content_body: str,
        platforms: list[str],
        marketplace_credentials: dict,
        revenue_webhook_url: str | None = None,
        tags: list[str] | None = None,
        price_usd: float | None = None,
    ) -> dict | None:
        """Send content to Content Production for marketplace distribution.

        Args:
            title: Content title.
            description: Short description / subtitle.
            content_body: The full content text.
            platforms: Target marketplace platforms (gumroad, etsy, shopify, amazon-kdp).
            marketplace_credentials: Customer's API keys per platform.
            revenue_webhook_url: URL where CP should POST revenue events back.
            tags: Optional tags for the listing.
            price_usd: Optional price in USD.

        Returns:
            Dict with job_id and status on success, None on failure.
        """
        payload: dict = {
            "title": title,
            "description": description,
            "content_body": content_body,
            "platforms": platforms,
            "marketplace_credentials": marketplace_credentials,
            "tags": tags or [],
        }
        if price_usd is not None:
            payload["price_usd"] = price_usd
        if revenue_webhook_url:
            payload["revenue_webhook_url"] = revenue_webhook_url
        return self._request("POST", "/v1/distribute", payload)

    def get_distribution_status(self, job_id: str) -> dict | None:
        """Check the status of a distribution job.

        Returns dict with status info, or None on failure.
        """
        return self._request("GET", f"/v1/distribute/{job_id}/status")

    def produce_ebook(
        self,
        topic: str,
        category: str = "general",
        audience: str = "",
        priority: float = 0.5,
        marketplace_credentials: dict | None = None,
        revenue_webhook_url: str | None = None,
    ) -> dict | None:
        """Submit an ebook production job to Content Production.

        Args:
            topic: The ebook topic / title.
            category: Content category (e.g. "technology", "business").
            audience: Target audience description.
            priority: Priority score 0-1 (higher = more urgent).
            marketplace_credentials: Optional marketplace API keys for distribution.
            revenue_webhook_url: URL where CP should POST revenue events back.

        Returns:
            Dict with job info on success, None on failure.
        """
        payload: dict = {
            "topic": topic,
            "category": category,
            "audience": audience,
            "priority": priority,
        }
        if marketplace_credentials:
            payload["marketplace_credentials"] = marketplace_credentials
        if revenue_webhook_url:
            payload["revenue_webhook_url"] = revenue_webhook_url
        return self._request("POST", "/v1/production/produce", payload)

    def priority_produce(
        self,
        topic: str,
        category: str = "general",
        service_token: str = "",
    ) -> dict | None:
        """Submit a priority ebook production job (bypasses queue).

        Uses the provided service_token in the X-Service-Token header
        to authenticate with Content Production's priority endpoint.

        Returns:
            Dict with priority job info on success, None on failure.
        """
        url = f"{self._base_url}/v1/priority/produce"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Service-Token": service_token,
            "X-Service-Name": "arclane",
        }
        body = {"topic": topic, "category": category}
        data = json.dumps(body).encode("utf-8")

        try:
            if _HAS_RESILIENCE:
                result = resilient_fetch(
                    url,
                    method="POST",
                    headers=headers,
                    data=data,
                    timeout=CP_TIMEOUT,
                    retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_cp_cb,
                )
                return result
            else:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=int(CP_TIMEOUT)) as resp:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log.warning("Content Production priority produce failed: %s", exc)
            return None

    def get_production_status(self) -> dict | None:
        """Get overall production line status from Content Production.

        Returns:
            Dict with active_jobs, queued_jobs, completed_jobs, metrics on success.
        """
        return self._request("GET", "/v1/production/status")

    def get_job_status(self, job_id: str | None = None) -> dict | None:
        """Get production job list from Content Production.

        Returns the full job list (active, queued, completed). Caller can filter
        by job_id or topic as needed.
        """
        return self._request("GET", "/v1/production/jobs")

    def health_check(self) -> bool:
        """Check if Content Production service is reachable.

        Returns True if healthy, False otherwise.
        """
        result = self._request("GET", "/health")
        return result is not None
