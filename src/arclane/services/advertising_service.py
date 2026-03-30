"""Advertising service — AI-powered ad copy generation, customer segmentation, and campaign management."""

import json
from datetime import datetime, timezone
from textwrap import dedent

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.engine.llm_client import ArclaneLLMClient
from arclane.integrations.google_ads_client import GoogleAdsClient
from arclane.integrations.linkedin_ads_client import LinkedInAdsClient
from arclane.integrations.meta_ads_client import MetaAdsClient
from arclane.integrations.twitter_ads_client import TwitterAdsClient
from arclane.models.tables import (
    AdCampaign,
    AdCopy,
    Business,
    CustomerSegment,
)

log = get_logger("advertising")

# Platform-specific format defaults
PLATFORM_FORMATS = {
    "google": "text",
    "facebook": "single_image",
    "instagram": "single_image",
    "linkedin": "single_image",
    "twitter": "text",
}

# Platform character limits
PLATFORM_LIMITS = {
    "google": {"headline": 30, "body": 90},
    "facebook": {"headline": 40, "body": 125},
    "instagram": {"headline": 40, "body": 125},
    "linkedin": {"headline": 70, "body": 150},
    "twitter": {"headline": 70, "body": 280},
}


async def generate_ad_copies(
    business: Business,
    session: AsyncSession,
    *,
    campaign_id: int | None = None,
    campaign_type: str = "awareness",
    tone: str = "professional",
    num_variations: int = 3,
    platform: str = "facebook",
    key_message: str | None = None,
    llm_client: ArclaneLLMClient | None = None,
) -> list[dict]:
    """Generate ad copy variations using LLM, tailored to the business and platform."""
    client = llm_client or ArclaneLLMClient()
    limits = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["facebook"])
    fmt = PLATFORM_FORMATS.get(platform, "single_image")

    # Gather business context
    segments = await _get_top_segments(business, session, limit=3)
    segment_context = ""
    if segments:
        segment_context = "\n".join(
            f"- {s.name}: {s.description}" for s in segments
        )

    system_prompt = dedent(f"""
        You are an expert advertising copywriter. Generate {num_variations} ad copy variations
        for the platform "{platform}" in a "{tone}" tone.

        Campaign type: {campaign_type}
        Platform limits: headline max {limits['headline']} chars, body max {limits['body']} chars.
        Ad format: {fmt}

        Business: {business.name}
        Description: {business.description}
        {f'Key message: {key_message}' if key_message else ''}
        {f'Target segments:\\n{segment_context}' if segment_context else ''}

        Return ONLY a JSON array of objects, each with:
        - "headline": compelling headline within char limit
        - "body": ad body text within char limit
        - "cta": call-to-action button text (e.g. "Learn More", "Shop Now", "Sign Up")
        - "image_prompt": a detailed image generation prompt for the ad visual

        Tailor copy to {campaign_type}:
        - awareness: brand story, introduce the problem/solution
        - traffic: curiosity hooks, value teasers, click incentives
        - conversion: urgency, social proof, clear value proposition, strong CTA
        - retargeting: remind of value, overcome objections, limited-time offers

        Make each variation meaningfully different in angle and hook.
    """).strip()

    result = await client.generate(
        system_prompt=system_prompt,
        user_prompt=f"Generate {num_variations} ad copy variations for {business.name}.",
        temperature=0.7,
        model=client.model_for_area("advertising"),
    )

    copies = _parse_ad_copies(result, num_variations, platform, fmt, tone)

    # Persist to database
    created = []
    for copy_data in copies:
        ad_copy = AdCopy(
            campaign_id=campaign_id,
            headline=copy_data["headline"][:500],
            body=copy_data["body"],
            cta=copy_data.get("cta"),
            image_prompt=copy_data.get("image_prompt"),
            platform_format=fmt,
            tone=tone,
        )
        session.add(ad_copy)
        created.append(copy_data)

    if created:
        await session.flush()
        log.info("Generated %d ad copies for %s on %s", len(created), business.slug, platform)

    return created


async def segment_customers(
    business: Business,
    session: AsyncSession,
    *,
    llm_client: ArclaneLLMClient | None = None,
) -> list[dict]:
    """Use LLM to identify and create customer segments for the business."""
    client = llm_client or ArclaneLLMClient()

    # Check existing segments to avoid duplicates
    existing = await session.execute(
        select(CustomerSegment).where(CustomerSegment.business_id == business.id)
    )
    existing_names = {s.name for s in existing.scalars().all()}

    system_prompt = dedent(f"""
        You are a customer segmentation expert. Analyze this business and identify
        3-5 distinct customer segments for advertising targeting.

        Business: {business.name}
        Description: {business.description}
        {f'Website: {business.website_url}' if business.website_url else ''}

        Return ONLY a JSON array of objects, each with:
        - "name": short segment name (e.g. "Budget-Conscious Parents", "Tech-Forward SMBs")
        - "description": 1-2 sentence segment description
        - "demographics": object with age_range, gender, location, income_level
        - "psychographics": object with interests (list), values (list), pain_points (list)
        - "behaviors": object with online_habits (list), buying_patterns (list)
        - "estimated_size": rough audience size (e.g. "100K-500K")
        - "priority": 1-10 (10=highest value)
        - "platform_targeting": object with recommended platforms and targeting params

        Focus on actionable segments that map to real ad platform targeting options.
        Prioritize by revenue potential and reachability.
    """).strip()

    result = await client.generate(
        system_prompt=system_prompt,
        user_prompt=f"Identify customer segments for {business.name}: {business.description}",
        temperature=0.4,
        model=client.model_for_area("advertising"),
    )

    segments = _parse_segments(result)

    created = []
    for seg_data in segments:
        if seg_data["name"] in existing_names:
            continue
        segment = CustomerSegment(
            business_id=business.id,
            name=seg_data["name"],
            description=seg_data.get("description", ""),
            demographics=seg_data.get("demographics"),
            psychographics=seg_data.get("psychographics"),
            behaviors=seg_data.get("behaviors"),
            estimated_size=seg_data.get("estimated_size"),
            priority=seg_data.get("priority", 0),
            platform_targeting=seg_data.get("platform_targeting"),
        )
        session.add(segment)
        created.append(seg_data)

    if created:
        await session.flush()
        log.info("Created %d customer segments for %s", len(created), business.slug)

    return created


async def create_campaign(
    business: Business,
    session: AsyncSession,
    *,
    name: str,
    platform: str,
    campaign_type: str = "awareness",
    budget_cents: int = 0,
    target_audience: dict | None = None,
    schedule: dict | None = None,
) -> AdCampaign:
    """Create a new ad campaign."""
    campaign = AdCampaign(
        business_id=business.id,
        name=name,
        platform=platform,
        campaign_type=campaign_type,
        budget_cents=budget_cents,
        target_audience=target_audience,
        schedule=schedule,
    )
    session.add(campaign)
    await session.flush()
    log.info("Created campaign '%s' for %s on %s", name, business.slug, platform)
    return campaign


async def launch_campaign(
    business: Business,
    campaign_id: int,
    session: AsyncSession,
    *,
    meta_client: MetaAdsClient | None = None,
    google_client: GoogleAdsClient | None = None,
    linkedin_client: LinkedInAdsClient | None = None,
    twitter_client: TwitterAdsClient | None = None,
) -> dict:
    """Validate, push to ad platform, and mark a campaign as active.

    Routes to the appropriate platform API based on campaign.platform:
    facebook/instagram → Meta, google → Google Ads, linkedin → LinkedIn,
    twitter → Twitter/X. Falls back to local activation if unconfigured.
    """
    campaign = await session.get(AdCampaign, campaign_id)
    if not campaign or campaign.business_id != business.id:
        return {"error": "Campaign not found"}

    if campaign.status == "active":
        return {"error": "Campaign is already active"}

    # Check for ad copies
    copies_result = await session.execute(
        select(AdCopy).where(AdCopy.campaign_id == campaign.id)
    )
    copies = copies_result.scalars().all()
    if not copies:
        return {"error": "Campaign needs at least one ad copy before launching"}

    # Check for segments
    segments_result = await session.execute(
        select(func.count(CustomerSegment.id)).where(
            CustomerSegment.business_id == business.id
        )
    )
    segments_count = segments_result.scalar() or 0

    # Apply top segments to campaign targeting if not already set
    if not campaign.target_audience:
        segments = await _get_top_segments(business, session, limit=3)
        if segments:
            campaign.target_audience = {
                "segments": [
                    {"name": s.name, "demographics": s.demographics, "psychographics": s.psychographics}
                    for s in segments
                ]
            }

    # Push to Meta for Facebook/Instagram campaigns
    meta_result = None
    if campaign.platform in ("facebook", "instagram"):
        client = meta_client or MetaAdsClient()
        if client.configured:
            ad_copies_data = [
                {"headline": c.headline, "body": c.body, "cta": c.cta}
                for c in copies
            ]
            # Determine platforms based on campaign platform
            platforms = (
                ["facebook", "instagram"]
                if campaign.platform == "facebook"
                else ["instagram"]
            )
            link_url = business.website_url or f"https://{business.slug}.arclane.cloud"
            daily_budget = (
                campaign.schedule.get("daily_budget", 500)
                if campaign.schedule
                else max(campaign.budget_cents // 30, 500)  # spread monthly budget
            )

            meta_result = await client.push_campaign(
                campaign_name=campaign.name,
                campaign_type=campaign.campaign_type,
                daily_budget_cents=daily_budget,
                ad_copies=ad_copies_data,
                targeting=campaign.target_audience,
                link_url=link_url,
                platforms=platforms,
            )

            if meta_result:
                # Store Meta IDs for future syncs
                campaign.metadata_json = {
                    **(campaign.metadata_json or {}),
                    "meta": meta_result,
                }
                # Mark individual copies as active
                for copy in copies:
                    copy.status = "active"
                log.info(
                    "Pushed campaign '%s' to Meta: %s (%d ads)",
                    campaign.name, meta_result.get("meta_campaign_id"), meta_result.get("total_ads", 0),
                )
            else:
                log.warning("Meta push failed for '%s' — marking active locally only", campaign.name)

    # Push to Google for Google campaigns
    google_result = None
    if campaign.platform == "google":
        g_client = google_client or GoogleAdsClient()
        if g_client.configured:
            ad_copies_data = [
                {"headline": c.headline, "body": c.body, "cta": c.cta}
                for c in copies
            ]
            link_url = business.website_url or f"https://{business.slug}.arclane.cloud"
            daily_budget = (
                campaign.schedule.get("daily_budget", 500)
                if campaign.schedule
                else max(campaign.budget_cents // 30, 500)
            )

            google_result = await g_client.push_campaign(
                campaign_name=campaign.name,
                campaign_type=campaign.campaign_type,
                daily_budget_cents=daily_budget,
                ad_copies=ad_copies_data,
                targeting=campaign.target_audience,
                link_url=link_url,
            )

            if google_result:
                campaign.metadata_json = {
                    **(campaign.metadata_json or {}),
                    "google": google_result,
                }
                for copy in copies:
                    copy.status = "active"
                log.info(
                    "Pushed campaign '%s' to Google Ads: %s (%d keywords)",
                    campaign.name,
                    google_result.get("google_campaign_resource_name"),
                    google_result.get("keywords_added", 0),
                )
            else:
                log.warning("Google Ads push failed for '%s' — marking active locally only", campaign.name)

    # Push to LinkedIn for LinkedIn campaigns
    linkedin_result = None
    if campaign.platform == "linkedin":
        li_client = linkedin_client or LinkedInAdsClient()
        if li_client.configured:
            ad_copies_data = [
                {"headline": c.headline, "body": c.body, "cta": c.cta}
                for c in copies
            ]
            link_url = business.website_url or f"https://{business.slug}.arclane.cloud"
            daily_budget = (
                campaign.schedule.get("daily_budget", 500)
                if campaign.schedule
                else max(campaign.budget_cents // 30, 500)
            )

            linkedin_result = await li_client.push_campaign(
                campaign_name=campaign.name,
                campaign_type=campaign.campaign_type,
                daily_budget_cents=daily_budget,
                ad_copies=ad_copies_data,
                targeting=campaign.target_audience,
                link_url=link_url,
            )

            if linkedin_result:
                campaign.metadata_json = {
                    **(campaign.metadata_json or {}),
                    "linkedin": linkedin_result,
                }
                for copy in copies:
                    copy.status = "active"
                log.info(
                    "Pushed campaign '%s' to LinkedIn: %s (%d creatives)",
                    campaign.name,
                    linkedin_result.get("linkedin_campaign_id"),
                    linkedin_result.get("total_creatives", 0),
                )
            else:
                log.warning("LinkedIn push failed for '%s' — marking active locally only", campaign.name)

    # Push to Twitter for Twitter campaigns
    twitter_result = None
    if campaign.platform == "twitter":
        tw_client = twitter_client or TwitterAdsClient()
        if tw_client.configured:
            ad_copies_data = [
                {"headline": c.headline, "body": c.body, "cta": c.cta}
                for c in copies
            ]
            link_url = business.website_url or f"https://{business.slug}.arclane.cloud"
            daily_budget = (
                campaign.schedule.get("daily_budget", 500)
                if campaign.schedule
                else max(campaign.budget_cents // 30, 500)
            )

            twitter_result = await tw_client.push_campaign(
                campaign_name=campaign.name,
                campaign_type=campaign.campaign_type,
                daily_budget_cents=daily_budget,
                ad_copies=ad_copies_data,
                targeting=campaign.target_audience,
                link_url=link_url,
            )

            if twitter_result:
                campaign.metadata_json = {
                    **(campaign.metadata_json or {}),
                    "twitter": twitter_result,
                }
                for copy in copies:
                    copy.status = "active"
                log.info(
                    "Pushed campaign '%s' to Twitter: %s (%d promoted tweets)",
                    campaign.name,
                    twitter_result.get("twitter_campaign_id"),
                    twitter_result.get("total_promoted", 0),
                )
            else:
                log.warning("Twitter push failed for '%s' — marking active locally only", campaign.name)

    campaign.status = "active"
    campaign.launched_at = datetime.now(timezone.utc)
    await session.flush()

    log.info(
        "Launched campaign '%s' for %s: %d copies, %d segments",
        campaign.name, business.slug, len(copies), segments_count,
    )

    result = {
        "campaign_id": campaign.id,
        "status": "active",
        "platform": campaign.platform,
        "ad_copies_count": len(copies),
        "segments_applied": segments_count,
        "message": f"Campaign '{campaign.name}' is now active on {campaign.platform}.",
    }
    if meta_result:
        result["meta"] = meta_result
    if google_result:
        result["google"] = google_result
    if linkedin_result:
        result["linkedin"] = linkedin_result
    if twitter_result:
        result["twitter"] = twitter_result
    return result


async def get_campaign_performance(
    business: Business,
    campaign_id: int,
    session: AsyncSession,
) -> dict:
    """Get performance summary for a campaign and its ad copies."""
    campaign = await session.get(AdCampaign, campaign_id)
    if not campaign or campaign.business_id != business.id:
        return {"error": "Campaign not found"}

    copies_result = await session.execute(
        select(AdCopy).where(AdCopy.campaign_id == campaign.id)
    )
    copies = copies_result.scalars().all()

    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "platform": campaign.platform,
            "status": campaign.status,
            "budget_cents": campaign.budget_cents,
            "spent_cents": campaign.spent_cents,
            "performance": campaign.performance or {},
        },
        "ad_copies": [
            {
                "id": c.id,
                "headline": c.headline,
                "status": c.status,
                "performance": c.performance or {},
            }
            for c in copies
        ],
        "total_copies": len(copies),
        "active_copies": sum(1 for c in copies if c.status == "active"),
    }


async def sync_campaign_performance(
    business: Business,
    campaign_id: int,
    session: AsyncSession,
    *,
    meta_client: MetaAdsClient | None = None,
    google_client: GoogleAdsClient | None = None,
    linkedin_client: LinkedInAdsClient | None = None,
    twitter_client: TwitterAdsClient | None = None,
) -> dict:
    """Pull latest performance data from the ad platform and update the campaign.

    Detects the platform from stored metadata and syncs from the appropriate API.
    """
    campaign = await session.get(AdCampaign, campaign_id)
    if not campaign or campaign.business_id != business.id:
        return {"error": "Campaign not found"}

    metadata = campaign.metadata_json or {}
    perf = None

    # Try Meta sync
    meta_ids = metadata.get("meta")
    if meta_ids:
        meta_campaign_id = meta_ids.get("meta_campaign_id")
        if meta_campaign_id:
            client = meta_client or MetaAdsClient()
            if client.configured:
                perf = await client.sync_campaign_performance(meta_campaign_id)

    # Try Google sync
    google_ids = metadata.get("google")
    if not perf and google_ids:
        google_campaign_rn = google_ids.get("google_campaign_resource_name")
        if google_campaign_rn:
            client = google_client or GoogleAdsClient()
            if client.configured:
                perf = await client.sync_campaign_performance(google_campaign_rn)

    # Try LinkedIn sync
    linkedin_ids = metadata.get("linkedin")
    if not perf and linkedin_ids:
        linkedin_campaign_id = linkedin_ids.get("linkedin_campaign_id")
        if linkedin_campaign_id:
            client = linkedin_client or LinkedInAdsClient()
            if client.configured:
                perf = await client.sync_campaign_performance(linkedin_campaign_id)

    # Try Twitter sync
    twitter_ids = metadata.get("twitter")
    if not perf and twitter_ids:
        twitter_campaign_id = twitter_ids.get("twitter_campaign_id")
        if twitter_campaign_id:
            client = twitter_client or TwitterAdsClient()
            if client.configured:
                perf = await client.sync_campaign_performance(twitter_campaign_id)

    if not perf:
        has_any = meta_ids or google_ids or linkedin_ids or twitter_ids
        if not has_any:
            return {"error": "Campaign has no ad platform integration — nothing to sync"}
        return {"error": "Failed to fetch performance data from ad platform"}

    # Update campaign performance and spend
    campaign.performance = perf
    campaign.spent_cents = perf.get("spend_cents", campaign.spent_cents)
    await session.flush()

    log.info(
        "Synced performance for '%s': %d impressions, %d clicks, $%.2f spent",
        campaign.name,
        perf.get("impressions", 0),
        perf.get("clicks", 0),
        perf.get("spend_cents", 0) / 100,
    )

    return {
        "campaign_id": campaign.id,
        "performance": perf,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


async def generate_full_campaign(
    business: Business,
    session: AsyncSession,
    *,
    platform: str = "facebook",
    campaign_type: str = "awareness",
    budget_cents: int = 0,
    llm_client: ArclaneLLMClient | None = None,
) -> dict:
    """End-to-end: create segments, campaign, and ad copies in one shot.

    Used by the nightly cycle to autonomously set up advertising.
    """
    # Step 1: Ensure customer segments exist
    existing_segments = await session.execute(
        select(func.count(CustomerSegment.id)).where(
            CustomerSegment.business_id == business.id
        )
    )
    if (existing_segments.scalar() or 0) == 0:
        await segment_customers(business, session, llm_client=llm_client)

    # Step 2: Create campaign
    campaign = await create_campaign(
        business, session,
        name=f"{business.name} — {campaign_type.title()} ({platform.title()})",
        platform=platform,
        campaign_type=campaign_type,
        budget_cents=budget_cents,
    )

    # Step 3: Generate ad copies
    copies = await generate_ad_copies(
        business, session,
        campaign_id=campaign.id,
        campaign_type=campaign_type,
        platform=platform,
        llm_client=llm_client,
    )

    # Step 4: Launch
    launch_result = await launch_campaign(business, campaign.id, session)

    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "platform": platform,
        "campaign_type": campaign_type,
        "copies_generated": len(copies),
        "launch": launch_result,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_top_segments(
    business: Business, session: AsyncSession, limit: int = 3
) -> list[CustomerSegment]:
    result = await session.execute(
        select(CustomerSegment)
        .where(CustomerSegment.business_id == business.id)
        .order_by(CustomerSegment.priority.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def _parse_ad_copies(
    raw: str | None,
    expected: int,
    platform: str,
    fmt: str,
    tone: str,
) -> list[dict]:
    """Parse LLM JSON output into ad copy dicts. Returns fallback on failure."""
    if not raw:
        return _fallback_copies(expected, platform, fmt, tone)
    try:
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        data = json.loads(text)
        if isinstance(data, list):
            return [
                {
                    "headline": item.get("headline", "")[:500],
                    "body": item.get("body", ""),
                    "cta": item.get("cta"),
                    "image_prompt": item.get("image_prompt"),
                }
                for item in data[:expected]
            ]
    except (json.JSONDecodeError, KeyError, TypeError):
        log.warning("Failed to parse ad copy LLM response, using fallback")
    return _fallback_copies(expected, platform, fmt, tone)


def _fallback_copies(count: int, platform: str, fmt: str, tone: str) -> list[dict]:
    """Deterministic fallback when LLM is unavailable."""
    angles = [
        ("Discover a better way", "See why customers are switching.", "Learn More"),
        ("Don't miss out", "Limited-time opportunity for your business.", "Get Started"),
        ("Results that speak", "Join thousands who already trust us.", "See Results"),
        ("Built for you", "The solution designed around your needs.", "Try Free"),
        ("Transform your workflow", "Spend less time on busywork.", "Start Now"),
    ]
    copies = []
    for i in range(min(count, len(angles))):
        headline, body, cta = angles[i]
        copies.append({
            "headline": headline,
            "body": body,
            "cta": cta,
            "image_prompt": None,
        })
    return copies


def _parse_segments(raw: str | None) -> list[dict]:
    """Parse LLM JSON output into segment dicts."""
    if not raw:
        return _fallback_segments()
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        data = json.loads(text)
        if isinstance(data, list):
            return data[:5]
    except (json.JSONDecodeError, KeyError, TypeError):
        log.warning("Failed to parse segment LLM response, using fallback")
    return _fallback_segments()


def _fallback_segments() -> list[dict]:
    """Deterministic fallback segments when LLM is unavailable."""
    return [
        {
            "name": "Early Adopters",
            "description": "Tech-savvy users who actively seek new solutions.",
            "demographics": {"age_range": "25-40"},
            "psychographics": {"interests": ["technology", "productivity"], "pain_points": ["inefficiency"]},
            "behaviors": {"online_habits": ["product hunt", "tech blogs"]},
            "estimated_size": "50K-200K",
            "priority": 8,
        },
        {
            "name": "Cost-Conscious Buyers",
            "description": "Price-sensitive buyers looking for the best value.",
            "demographics": {"age_range": "30-55"},
            "psychographics": {"interests": ["deals", "comparison shopping"], "pain_points": ["high costs"]},
            "behaviors": {"buying_patterns": ["coupon usage", "review checking"]},
            "estimated_size": "200K-500K",
            "priority": 6,
        },
        {
            "name": "Growth-Focused Professionals",
            "description": "Business owners and managers looking to scale.",
            "demographics": {"age_range": "30-50"},
            "psychographics": {"interests": ["business growth", "leadership"], "pain_points": ["scaling"]},
            "behaviors": {"online_habits": ["linkedin", "business podcasts"]},
            "estimated_size": "100K-300K",
            "priority": 7,
        },
    ]
