"""Content publishing service — coordinates publishing across platforms.

When content status changes to 'published', this service handles
distribution to registered channels (KH, future social APIs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from arclane.core.logging import get_logger
from arclane.integrations.kh_publisher import KHPublisher

log = get_logger("services.content_publisher")


@dataclass
class PublishResult:
    """Result of publishing content to a single channel."""
    channel: str
    success: bool
    url: str | None = None
    error: str | None = None
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PublishReport:
    """Aggregate result of publishing content across all channels."""
    content_id: int
    content_type: str
    channels_attempted: int = 0
    channels_succeeded: int = 0
    results: list[PublishResult] = field(default_factory=list)

    @property
    def fully_published(self) -> bool:
        return self.channels_attempted > 0 and self.channels_succeeded == self.channels_attempted


class ContentPublisher:
    """Publishes content to registered distribution channels.

    Currently supports:
    - Knowledge Harvester (artifact storage)

    Future channels (requires OAuth integration):
    - Twitter/X
    - LinkedIn
    - Bluesky
    """

    def __init__(self) -> None:
        self._kh = KHPublisher()
        self._publish_count = 0
        self._history: list[PublishReport] = []

    @property
    def publish_count(self) -> int:
        return self._publish_count

    @property
    def history(self) -> list[PublishReport]:
        return list(self._history[-100:])

    async def publish(
        self,
        content_id: int,
        content_type: str,
        title: str,
        body: str,
        business_name: str,
        platform: str | None = None,
    ) -> PublishReport:
        """Publish content to all applicable channels.

        Args:
            content_id: Database ID of the content.
            content_type: Type (blog, social, newsletter, etc).
            title: Content title.
            body: Content body.
            business_name: Name of the owning business.
            platform: Target platform hint (twitter, linkedin, etc).

        Returns:
            PublishReport with per-channel results.
        """
        report = PublishReport(content_id=content_id, content_type=content_type)

        # Channel 1: Knowledge Harvester
        kh_result = await self._publish_to_kh(
            content_type, title, body, business_name,
        )
        report.results.append(kh_result)
        report.channels_attempted += 1
        if kh_result.success:
            report.channels_succeeded += 1

        # Channel 2: Social platforms (stub for future OAuth integration)
        if platform and content_type == "social":
            social_result = self._stub_social_publish(platform, title, body)
            report.results.append(social_result)
            report.channels_attempted += 1
            if social_result.success:
                report.channels_succeeded += 1

        self._publish_count += 1
        self._history.append(report)
        if len(self._history) > 200:
            self._history = self._history[-100:]

        log.info(
            "Published content %d (%s): %d/%d channels",
            content_id, content_type,
            report.channels_succeeded, report.channels_attempted,
        )
        return report

    async def _publish_to_kh(
        self,
        content_type: str,
        title: str,
        body: str,
        business_name: str,
    ) -> PublishResult:
        """Publish to Knowledge Harvester as an artifact."""
        try:
            artifacts = await self._kh.publish_cycle_results(
                business_name=business_name,
                results=[{
                    "area": "content",
                    "status": "completed",
                    "result": body,
                    "content_type": content_type,
                    "content_title": title,
                }],
            )
            return PublishResult(
                channel="knowledge_harvester",
                success=True,
                url=None,
            )
        except Exception as exc:
            log.warning("KH publish failed: %s", exc)
            return PublishResult(
                channel="knowledge_harvester",
                success=False,
                error=str(exc),
            )

    def _stub_social_publish(
        self, platform: str, title: str, body: str,
    ) -> PublishResult:
        """Stub for future social media publishing.

        Returns a pending result — actual delivery requires OAuth tokens
        and platform API integration (Twitter, LinkedIn, Bluesky).
        """
        log.info(
            "Social publish queued for %s (requires OAuth integration): %s",
            platform, title[:50],
        )
        return PublishResult(
            channel=platform,
            success=False,
            error=f"{platform} publishing requires OAuth integration (not yet configured)",
        )

    async def publish_with_distribution(
        self,
        content_id: int,
        content_type: str,
        title: str,
        body: str,
        business_name: str,
        business_id: int,
        session: "AsyncSession",
        platform: str | None = None,
    ) -> PublishReport:
        """Publish content to KH and all active distribution channels.

        This extends publish() by also checking the distribution_channels table
        and distributing to any configured channels.
        """
        # Standard publish (KH + social stub)
        report = await self.publish(
            content_id=content_id,
            content_type=content_type,
            title=title,
            body=body,
            business_name=business_name,
            platform=platform,
        )

        # Also distribute through registered channels
        try:
            from arclane.services.distribution_service import distribute_content as dist_fn
            from arclane.models.tables import Business, Content

            business = await session.get(Business, business_id)
            content = await session.get(Content, content_id)

            if business and content:
                dist_result = await dist_fn(business, content, session)
                channels = dist_result.get("channels", {})
                for ch_name, ch_result in channels.items():
                    success = ch_result.get("status") == "distributed"
                    report.results.append(PublishResult(
                        channel=f"dist:{ch_name}",
                        success=success,
                        error=ch_result.get("error"),
                    ))
                    report.channels_attempted += 1
                    if success:
                        report.channels_succeeded += 1
        except Exception as exc:
            log.warning("Distribution channel publish failed: %s", exc)

        return report

    def stats(self) -> dict[str, Any]:
        total = len(self._history)
        fully = sum(1 for r in self._history if r.fully_published)
        return {
            "total_publishes": total,
            "fully_published": fully,
            "partial": total - fully,
        }


# Singleton
content_publisher = ContentPublisher()
