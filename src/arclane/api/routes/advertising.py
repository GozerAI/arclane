"""Advertising API — campaigns, ad copy generation, customer segments."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.models.schemas import (
    AdCopyGenerate,
    AdCopyResponse,
    CampaignCreate,
    CampaignResponse,
    CustomerSegmentResponse,
)
from arclane.models.tables import AdCampaign, AdCopy, Business, CustomerSegment
from arclane.services.advertising_service import (
    create_campaign,
    generate_ad_copies,
    generate_full_campaign,
    get_campaign_performance,
    launch_campaign,
    segment_customers,
    sync_campaign_performance,
)

router = APIRouter()


# --- Campaigns ---


@router.get("/campaigns")
async def list_campaigns(
    status: str | None = None,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List all ad campaigns for the business."""
    query = select(AdCampaign).where(AdCampaign.business_id == business.id)
    if status:
        query = query.where(AdCampaign.status == status)
    query = query.order_by(AdCampaign.created_at.desc()).limit(50)
    result = await session.execute(query)
    campaigns = result.scalars().all()
    return {"campaigns": [CampaignResponse.model_validate(c).model_dump() for c in campaigns]}


@router.post("/campaigns", status_code=201)
async def create_new_campaign(
    payload: CampaignCreate,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Create a new ad campaign."""
    campaign = await create_campaign(
        business, session,
        name=payload.name,
        platform=payload.platform,
        campaign_type=payload.campaign_type,
        budget_cents=payload.budget_cents,
        target_audience=payload.target_audience,
        schedule=payload.schedule,
    )
    await session.commit()
    return CampaignResponse.model_validate(campaign).model_dump()


@router.get("/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get a single campaign with performance data."""
    perf = await get_campaign_performance(business, campaign_id, session)
    if "error" in perf:
        raise HTTPException(status_code=404, detail=perf["error"])
    return perf


@router.post("/campaigns/{campaign_id}/launch")
async def launch(
    campaign_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Validate and launch a campaign."""
    result = await launch_campaign(business, campaign_id, session)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    await session.commit()
    return result


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Pause an active campaign."""
    campaign = await session.get(AdCampaign, campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "active":
        raise HTTPException(status_code=400, detail="Only active campaigns can be paused")
    campaign.status = "paused"
    await session.commit()
    return {"status": "paused", "campaign_id": campaign_id}


@router.post("/campaigns/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Resume a paused campaign."""
    campaign = await session.get(AdCampaign, campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != "paused":
        raise HTTPException(status_code=400, detail="Only paused campaigns can be resumed")
    campaign.status = "active"
    await session.commit()
    return {"status": "active", "campaign_id": campaign_id}


@router.post("/campaigns/{campaign_id}/sync")
async def sync_performance(
    campaign_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Pull latest performance data from Meta and update the campaign."""
    result = await sync_campaign_performance(business, campaign_id, session)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    await session.commit()
    return result


# --- Ad Copies ---


@router.post("/campaigns/{campaign_id}/copies")
async def generate_copies(
    campaign_id: int,
    payload: AdCopyGenerate,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Generate ad copy variations for a campaign."""
    campaign = await session.get(AdCampaign, campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    copies = await generate_ad_copies(
        business, session,
        campaign_id=campaign.id,
        campaign_type=payload.campaign_type,
        tone=payload.tone,
        num_variations=payload.num_variations,
        platform=payload.platform,
        key_message=payload.key_message,
    )
    await session.commit()
    return {"copies": copies, "count": len(copies)}


@router.get("/campaigns/{campaign_id}/copies")
async def list_copies(
    campaign_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List all ad copies for a campaign."""
    campaign = await session.get(AdCampaign, campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    result = await session.execute(
        select(AdCopy).where(AdCopy.campaign_id == campaign.id).order_by(AdCopy.created_at.desc())
    )
    copies = result.scalars().all()
    return {"copies": [AdCopyResponse.model_validate(c).model_dump() for c in copies]}


@router.post("/copies/{copy_id}/approve")
async def approve_copy(
    copy_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Approve an ad copy for use in a campaign."""
    ad_copy = await session.get(AdCopy, copy_id)
    if not ad_copy:
        raise HTTPException(status_code=404, detail="Ad copy not found")
    # Verify ownership through campaign
    campaign = await session.get(AdCampaign, ad_copy.campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Ad copy not found")
    ad_copy.status = "approved"
    await session.commit()
    return {"status": "approved", "copy_id": copy_id}


@router.post("/copies/{copy_id}/reject")
async def reject_copy(
    copy_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Reject an ad copy."""
    ad_copy = await session.get(AdCopy, copy_id)
    if not ad_copy:
        raise HTTPException(status_code=404, detail="Ad copy not found")
    campaign = await session.get(AdCampaign, ad_copy.campaign_id)
    if not campaign or campaign.business_id != business.id:
        raise HTTPException(status_code=404, detail="Ad copy not found")
    ad_copy.status = "rejected"
    await session.commit()
    return {"status": "rejected", "copy_id": copy_id}


# --- Customer Segments ---


@router.get("/segments")
async def list_segments(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List customer segments for the business."""
    result = await session.execute(
        select(CustomerSegment)
        .where(CustomerSegment.business_id == business.id)
        .order_by(CustomerSegment.priority.desc())
    )
    segments = result.scalars().all()
    return {"segments": [CustomerSegmentResponse.model_validate(s).model_dump() for s in segments]}


@router.post("/segments")
async def generate_segments(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Generate customer segments using AI analysis."""
    segments = await segment_customers(business, session)
    await session.commit()
    return {"segments": segments, "count": len(segments)}


@router.delete("/segments/{segment_id}")
async def delete_segment(
    segment_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Delete a customer segment."""
    segment = await session.get(CustomerSegment, segment_id)
    if not segment or segment.business_id != business.id:
        raise HTTPException(status_code=404, detail="Segment not found")
    await session.delete(segment)
    await session.commit()
    return {"status": "deleted", "segment_id": segment_id}


# --- Full Campaign Generation ---


@router.post("/generate")
async def generate_full(
    payload: CampaignCreate,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """End-to-end: create segments + campaign + ad copies in one request."""
    result = await generate_full_campaign(
        business, session,
        platform=payload.platform,
        campaign_type=payload.campaign_type,
        budget_cents=payload.budget_cents,
    )
    await session.commit()
    return result
