"""Content distribution — channel management and auto-publishing."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.integrations.content_production_client import (
    ContentProductionClient,
    MARKETPLACE_PLATFORMS,
)
from arclane.models.tables import Business, Content, DistributionChannel

log = get_logger("distribution")

_cp_client: ContentProductionClient | None = None


def _get_cp_client() -> ContentProductionClient:
    global _cp_client
    if _cp_client is None:
        _cp_client = ContentProductionClient()
    return _cp_client


async def configure_channel(
    business: Business,
    session: AsyncSession,
    *,
    platform: str,
    config: dict | None = None,
) -> DistributionChannel:
    """Add or update a distribution channel for a business."""
    result = await session.execute(
        select(DistributionChannel).where(
            DistributionChannel.business_id == business.id,
            DistributionChannel.platform == platform,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.config_json = config or existing.config_json
        existing.status = "active"
        await session.flush()
        return existing

    channel = DistributionChannel(
        business_id=business.id,
        platform=platform,
        config_json=config,
        status="active",
    )
    session.add(channel)
    await session.flush()
    log.info("Distribution channel %s configured for %s", platform, business.slug)
    return channel


async def get_channels(business: Business, session: AsyncSession) -> list[dict]:
    """Return all distribution channels for a business."""
    result = await session.execute(
        select(DistributionChannel)
        .where(DistributionChannel.business_id == business.id)
        .order_by(DistributionChannel.created_at)
    )
    channels = result.scalars().all()
    return [
        {
            "id": ch.id,
            "platform": ch.platform,
            "status": ch.status,
            "last_published_at": ch.last_published_at.isoformat() if ch.last_published_at else None,
            "created_at": ch.created_at.isoformat(),
        }
        for ch in channels
    ]


async def distribute_content(
    business: Business,
    content: Content,
    session: AsyncSession,
    platforms: list[str] | None = None,
) -> dict:
    """Distribute a content item to configured channels."""
    result = await session.execute(
        select(DistributionChannel).where(
            DistributionChannel.business_id == business.id,
            DistributionChannel.status == "active",
        )
    )
    channels = result.scalars().all()

    if platforms:
        channels = [ch for ch in channels if ch.platform in platforms]

    # Separate marketplace channels from regular channels
    marketplace_channels = [ch for ch in channels if ch.platform in MARKETPLACE_PLATFORMS]
    regular_channels = [ch for ch in channels if ch.platform not in MARKETPLACE_PLATFORMS]

    results = {}

    # Handle marketplace channels via Content Production service
    if marketplace_channels:
        mp_result = await _distribute_via_content_production(
            business, content, marketplace_channels,
        )
        results.update(mp_result)
        for ch in marketplace_channels:
            if results.get(ch.platform, {}).get("status") == "distributed":
                ch.last_published_at = datetime.now(timezone.utc)

    # Handle regular channels with the existing stub
    for channel in regular_channels:
        try:
            # Stub: actual publishing would call platform APIs
            publish_result = await _publish_to_channel(channel, content)
            results[channel.platform] = {"status": "distributed", "result": publish_result}
            channel.last_published_at = datetime.now(timezone.utc)
        except Exception as exc:
            log.warning("Distribution to %s failed for content %d: %s", channel.platform, content.id, exc)
            results[channel.platform] = {"status": "failed", "error": str(exc)}

    # Update content distribution status
    all_success = all(r["status"] == "distributed" for r in results.values())
    content.distribution_status = "distributed" if all_success else ("failed" if not results else "partial")
    content.distribution_results = results

    await session.flush()
    return {"content_id": content.id, "channels": results}


async def get_distribution_stats(business: Business, session: AsyncSession) -> dict:
    """Return distribution statistics for a business."""
    from sqlalchemy import func

    # Channel stats
    channels = await get_channels(business, session)

    # Content distribution counts
    distributed = (await session.execute(
        select(func.count(Content.id)).where(
            Content.business_id == business.id,
            Content.distribution_status == "distributed",
        )
    )).scalar() or 0

    pending = (await session.execute(
        select(func.count(Content.id)).where(
            Content.business_id == business.id,
            Content.distribution_status == "pending",
        )
    )).scalar() or 0

    total = (await session.execute(
        select(func.count(Content.id)).where(Content.business_id == business.id)
    )).scalar() or 0

    return {
        "channels": channels,
        "channel_count": len(channels),
        "content_distributed": distributed,
        "content_pending": pending,
        "content_total": total,
    }


async def _distribute_via_content_production(
    business: Business,
    content: Content,
    marketplace_channels: list[DistributionChannel],
) -> dict[str, dict]:
    """Delegate marketplace distribution to the Content Production service.

    Reads marketplace_credentials from business.agent_config and sends the
    content to CP with the customer's own API keys.
    """
    agent_config = business.agent_config or {}
    credentials = agent_config.get("marketplace_credentials", {})
    platforms = [ch.platform for ch in marketplace_channels]

    # Build revenue webhook URL so CP can send revenue events back
    revenue_webhook_url = f"http://localhost:8012/api/businesses/{business.slug}/webhooks/revenue"

    client = _get_cp_client()
    result = client.distribute_content(
        title=content.title or "Untitled",
        description=(content.metadata_json or {}).get("description", ""),
        content_body=content.body,
        platforms=platforms,
        marketplace_credentials=credentials,
        revenue_webhook_url=revenue_webhook_url,
        tags=(content.metadata_json or {}).get("tags", []),
        price_usd=(content.metadata_json or {}).get("price_usd"),
    )

    results: dict[str, dict] = {}
    if result:
        for platform in platforms:
            results[platform] = {
                "status": "distributed",
                "result": result,
            }
        log.info(
            "Marketplace distribution via CP for content %d to %s (job=%s)",
            content.id,
            platforms,
            result.get("job_id", "unknown"),
        )
    else:
        for platform in platforms:
            results[platform] = {
                "status": "failed",
                "error": "Content Production service unavailable",
            }
        log.warning("Content Production distribution failed for content %d", content.id)

    return results


async def _publish_to_channel(channel: DistributionChannel, content: Content) -> dict:
    """Stub for actual platform publishing. Returns a simulated result."""
    return {
        "platform": channel.platform,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "content_id": content.id,
        "status": "published",
    }
