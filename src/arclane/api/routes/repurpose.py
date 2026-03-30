"""Content repurposing API — transform content between formats."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.tables import Business, Content
from arclane.services.content_repurposer import available_formats, repurpose

router = APIRouter()


@router.get("/{content_id}/formats")
async def get_available_formats(
    content_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List available repurposing formats for a content item."""
    content = await session.get(Content, content_id)
    if not content or content.business_id != business.id:
        raise HTTPException(status_code=404, detail="Content not found")
    return {"formats": available_formats(content.content_type)}


@router.post("/{content_id}/repurpose/{target_format}")
async def repurpose_content(
    content_id: int,
    target_format: str,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Repurpose a content item into a different format."""
    content = await session.get(Content, content_id)
    if not content or content.business_id != business.id:
        raise HTTPException(status_code=404, detail="Content not found")

    result = repurpose(content.content_type, content.title or "", content.body, target_format)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Optionally save as new content
    new_content = Content(
        business_id=business.id,
        content_type=_map_format_to_type(target_format),
        title=result["title"],
        body=result["body"],
        status="draft",
        metadata_json={"repurposed_from": content_id, "format": target_format},
    )
    session.add(new_content)
    await session.commit()
    await session.refresh(new_content)

    return {
        "id": new_content.id,
        "format": result["format"],
        "title": result["title"],
        "piece_count": result.get("piece_count", 1),
        "body_preview": result["body"][:500],
    }


def _map_format_to_type(target_format: str) -> str:
    """Map repurposing format to content_type."""
    mapping = {
        "twitter_thread": "social",
        "linkedin_carousel": "social",
        "executive_summary": "report",
        "blog_expansion": "blog",
        "markdown": "blog",
        "email_variant": "newsletter",
        "key_takeaways": "report",
        "quote_cards": "social",
    }
    return mapping.get(target_format, "blog")
