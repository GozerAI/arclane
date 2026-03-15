"""Background cycle execution with webhook notification.

Item 142: Executes cycles in the background and sends webhook notifications
to a registered URL when the cycle completes or fails.
"""

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("performance.webhook_cycles")


@dataclass
class WebhookConfig:
    """Configuration for cycle completion webhooks."""
    url: str
    secret: str = ""
    timeout_s: float = 15.0
    retry_count: int = 3
    retry_delay_s: float = 5.0


@dataclass
class WebhookDelivery:
    """Record of a webhook delivery attempt."""
    webhook_url: str
    event: str
    payload: dict
    status_code: int | None = None
    success: bool = False
    attempts: int = 0
    error: str | None = None
    delivered_at: float = field(default_factory=time.time)


class CycleWebhookNotifier:
    """Sends webhook notifications for cycle lifecycle events."""

    def __init__(self):
        self._webhooks: dict[int, WebhookConfig] = {}  # business_id -> config
        self._deliveries: list[WebhookDelivery] = []
        self._delivery_count = 0

    @property
    def deliveries(self) -> list[WebhookDelivery]:
        return list(self._deliveries)

    @property
    def delivery_count(self) -> int:
        return self._delivery_count

    def register_webhook(self, business_id: int, config: WebhookConfig) -> None:
        """Register a webhook URL for a business."""
        self._webhooks[business_id] = config
        log.info("Webhook registered for business %d: %s", business_id, config.url)

    def unregister_webhook(self, business_id: int) -> None:
        """Remove webhook registration for a business."""
        self._webhooks.pop(business_id, None)

    def get_webhook(self, business_id: int) -> WebhookConfig | None:
        return self._webhooks.get(business_id)

    def _sign_payload(self, payload: bytes, secret: str) -> str:
        """Generate HMAC-SHA256 signature for webhook payload."""
        return hmac.new(
            secret.encode(), payload, hashlib.sha256,
        ).hexdigest()

    async def notify(
        self,
        business_id: int,
        event: str,
        data: dict,
    ) -> WebhookDelivery | None:
        """Send a webhook notification for a cycle event.

        Args:
            business_id: Business that owns the cycle.
            event: Event type (e.g., "cycle.completed", "cycle.failed").
            data: Event payload.

        Returns:
            WebhookDelivery record, or None if no webhook registered.
        """
        config = self._webhooks.get(business_id)
        if not config:
            return None

        payload = {
            "event": event,
            "business_id": business_id,
            "timestamp": time.time(),
            "data": data,
        }
        payload_bytes = json.dumps(payload, default=str).encode()

        delivery = WebhookDelivery(
            webhook_url=config.url,
            event=event,
            payload=payload,
        )

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Arclane-Event": event,
        }
        if config.secret:
            headers["X-Webhook-Signature"] = self._sign_payload(payload_bytes, config.secret)

        # Retry loop
        for attempt in range(1, config.retry_count + 1):
            delivery.attempts = attempt
            try:
                async with httpx.AsyncClient(timeout=config.timeout_s) as client:
                    resp = await client.post(
                        config.url,
                        content=payload_bytes,
                        headers=headers,
                    )
                    delivery.status_code = resp.status_code
                    if 200 <= resp.status_code < 300:
                        delivery.success = True
                        break
                    else:
                        delivery.error = f"HTTP {resp.status_code}"
            except Exception as exc:
                delivery.error = str(exc)

            if attempt < config.retry_count:
                await asyncio.sleep(config.retry_delay_s * attempt)

        self._deliveries.append(delivery)
        self._delivery_count += 1
        # Keep last 500 deliveries
        if len(self._deliveries) > 500:
            self._deliveries = self._deliveries[-250:]

        if delivery.success:
            log.info("Webhook delivered: %s to %s", event, config.url)
        else:
            log.warning(
                "Webhook failed after %d attempts: %s to %s — %s",
                delivery.attempts, event, config.url, delivery.error,
            )

        return delivery

    async def notify_cycle_complete(
        self,
        business_id: int,
        cycle_id: int,
        status: str,
        result: dict | None = None,
    ) -> WebhookDelivery | None:
        """Convenience: notify when a cycle completes or fails."""
        event = f"cycle.{status}"
        return await self.notify(business_id, event, {
            "cycle_id": cycle_id,
            "status": status,
            "result_summary": {
                k: v for k, v in (result or {}).items()
                if k in ("total", "completed", "failed", "status")
            },
        })

    def stats(self) -> dict:
        total = len(self._deliveries)
        succeeded = sum(1 for d in self._deliveries if d.success)
        return {
            "registered_webhooks": len(self._webhooks),
            "total_deliveries": total,
            "successful": succeeded,
            "failed": total - succeeded,
        }


# Singleton
cycle_webhook_notifier = CycleWebhookNotifier()
