"""Distribution API — channel management and content publishing."""

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business, Content
from arclane.services.distribution_service import (
    configure_channel,
    distribute_content,
    get_channels,
    get_distribution_stats,
)

router = APIRouter()

AVAILABLE_MARKETPLACES = ["gumroad", "etsy", "shopify", "amazon_kdp"]

# Mapping of credential body keys to marketplace names
_CREDENTIAL_PLATFORM_MAP = {
    "gumroad_api_token": "gumroad",
    "etsy_api_key": "etsy",
    "shopify_store_url": "shopify",
    "shopify_api_token": "shopify",
    "amazon_kdp_api_key": "amazon_kdp",
}


class ChannelCreate(BaseModel):
    platform: str = Field(..., max_length=100)
    config: dict | None = None


class MarketplaceCredentials(BaseModel):
    """Marketplace API credentials. Only include keys you want to set/update."""
    gumroad_api_token: str | None = None
    etsy_api_key: str | None = None
    shopify_store_url: str | None = None
    shopify_api_token: str | None = None
    amazon_kdp_api_key: str | None = None


@router.post("/channels")
async def add_channel(
    payload: ChannelCreate,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Add or update a distribution channel."""
    channel = await configure_channel(business, session, platform=payload.platform, config=payload.config)
    await session.commit()
    return {"id": channel.id, "platform": channel.platform, "status": channel.status}


@router.get("/channels")
async def list_channels(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List all distribution channels."""
    return {"channels": await get_channels(business, session)}


@router.post("/publish/{content_id}")
async def publish_content(
    content_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Distribute a content item to configured channels."""
    content = await session.get(Content, content_id)
    if not content or content.business_id != business.id:
        raise HTTPException(status_code=404, detail="Content not found")
    result = await distribute_content(business, content, session)
    await session.commit()
    return result


@router.get("/stats")
async def distribution_stats(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get distribution statistics."""
    return await get_distribution_stats(business, session)


# --- Marketplace Credential Management ---


@router.put("/marketplace/credentials")
async def set_marketplace_credentials(
    payload: MarketplaceCredentials,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Store marketplace API keys in agent_config.

    Only non-null fields in the payload are stored/updated.
    Existing credentials for other platforms are preserved.
    """
    agent_config = dict(business.agent_config or {})
    existing_creds = dict(agent_config.get("marketplace_credentials", {}))

    # Merge non-None values
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    existing_creds.update(updates)

    agent_config["marketplace_credentials"] = existing_creds
    business.agent_config = agent_config
    await session.flush()
    await session.commit()

    # Return which platforms are now configured (without revealing keys)
    configured = _get_configured_platforms(existing_creds)
    return {"configured": configured, "available": AVAILABLE_MARKETPLACES}


@router.get("/marketplace/credentials")
async def get_marketplace_credentials(
    business: Business = Depends(get_business),
):
    """Get which marketplace platforms have credentials configured.

    Returns platform names only, NOT the raw API keys.
    """
    agent_config = business.agent_config or {}
    creds = agent_config.get("marketplace_credentials", {})
    configured = _get_configured_platforms(creds)
    return {"configured": configured, "available": AVAILABLE_MARKETPLACES}


@router.delete("/marketplace/credentials/{platform}")
async def delete_marketplace_credential(
    platform: str = Path(...),
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Remove all credentials for a specific marketplace platform."""
    agent_config = dict(business.agent_config or {})
    creds = dict(agent_config.get("marketplace_credentials", {}))

    # Remove all keys belonging to this platform
    keys_to_remove = [k for k, v in _CREDENTIAL_PLATFORM_MAP.items() if v == platform]
    removed = False
    for key in keys_to_remove:
        if key in creds:
            del creds[key]
            removed = True

    if not removed:
        raise HTTPException(status_code=404, detail=f"No credentials found for platform: {platform}")

    agent_config["marketplace_credentials"] = creds
    business.agent_config = agent_config
    await session.flush()
    await session.commit()
    return {"status": "deleted", "platform": platform}


def _get_configured_platforms(creds: dict) -> list[str]:
    """Return deduplicated list of platforms that have at least one credential set."""
    platforms = set()
    for key, value in creds.items():
        if value and key in _CREDENTIAL_PLATFORM_MAP:
            platforms.add(_CREDENTIAL_PLATFORM_MAP[key])
    return sorted(platforms)
