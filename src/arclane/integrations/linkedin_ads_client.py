"""LinkedIn Marketing API client — creates and manages LinkedIn ad campaigns.

Wraps the LinkedIn Marketing API v2 (restli) to:
  1. Create campaigns, creatives, and sponsored content
  2. Target by job title, industry, company size, seniority, skills
  3. Sync performance metrics back to Arclane
  4. Pause/resume campaigns

Requires:
  - ARCLANE_LINKEDIN_ADS_ACCESS_TOKEN   (OAuth2 access token with rw_ads scope)
  - ARCLANE_LINKEDIN_ADS_ACCOUNT_ID     (Sponsored account ID, numeric)

Graceful degradation: all methods return None/empty on failure.
"""

import logging

import httpx

from arclane.core.config import settings

try:
    from gozerai_telemetry.resilience import (
        resilient_request,
        get_circuit_breaker,
        DEFAULT_RETRY,
    )
    _HAS_RESILIENCE = True
    _li_cb = get_circuit_breaker("linkedin_ads", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _li_cb = None

log = logging.getLogger("arclane.integrations.linkedin_ads")

LINKEDIN_API_URL = "https://api.linkedin.com/rest"
LINKEDIN_TIMEOUT = 15.0

# Map Arclane campaign types to LinkedIn objectives
OBJECTIVE_MAP = {
    "awareness": "BRAND_AWARENESS",
    "traffic": "WEBSITE_VISITS",
    "conversion": "WEBSITE_CONVERSIONS",
    "retargeting": "WEBSITE_CONVERSIONS",
}

STATUS_MAP = {
    "active": "ACTIVE",
    "paused": "PAUSED",
    "draft": "DRAFT",
}

# LinkedIn ad format types
FORMAT_MAP = {
    "single_image": "SINGLE_IMAGE",
    "carousel": "CAROUSEL",
    "video": "VIDEO",
    "text": "TEXT_AD",
}


class LinkedInAdsClient:
    """Async client for the LinkedIn Marketing API.

    All public methods gracefully degrade: return None on failure, never raise.
    """

    def __init__(
        self,
        access_token: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
    ):
        self._token = access_token or settings.linkedin_ads_access_token
        self._account_id = account_id or settings.linkedin_ads_account_id
        self._base_url = (base_url or LINKEDIN_API_URL).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._token and self._account_id)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202401",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict | None:
        url = f"{self._base_url}/{path}"
        try:
            if _HAS_RESILIENCE:
                qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
                full_url = f"{url}?{qs}" if qs else url
                return await resilient_request(
                    "GET", full_url, headers=self._headers(),
                    timeout=LINKEDIN_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_li_cb,
                )
            async with httpx.AsyncClient(timeout=LINKEDIN_TIMEOUT) as client:
                resp = await client.get(url, params=params, headers=self._headers())
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("LinkedIn GET %s failed: %s", path, exc)
            return None

    async def _post(self, path: str, body: dict) -> dict | None:
        url = f"{self._base_url}/{path}"
        try:
            if _HAS_RESILIENCE:
                return await resilient_request(
                    "POST", url, json_body=body, headers=self._headers(),
                    timeout=LINKEDIN_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_li_cb,
                )
            async with httpx.AsyncClient(timeout=LINKEDIN_TIMEOUT) as client:
                resp = await client.post(url, json=body, headers=self._headers())
                resp.raise_for_status()
                # LinkedIn returns 201 with X-RestLi-Id header for creates
                result = {}
                if resp.headers.get("X-RestLi-Id"):
                    result["id"] = resp.headers["X-RestLi-Id"]
                if resp.content:
                    try:
                        result.update(resp.json())
                    except Exception:
                        pass
                return result
        except Exception as exc:
            log.warning("LinkedIn POST %s failed: %s", path, exc)
            return None

    async def _patch(self, path: str, body: dict) -> dict | None:
        url = f"{self._base_url}/{path}"
        try:
            async with httpx.AsyncClient(timeout=LINKEDIN_TIMEOUT) as client:
                resp = await client.patch(url, json=body, headers=self._headers())
                resp.raise_for_status()
                return {"success": True}
        except Exception as exc:
            log.warning("LinkedIn PATCH %s failed: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Campaign Management
    # ------------------------------------------------------------------

    async def create_campaign_group(self, name: str) -> dict | None:
        """Create a campaign group (container for campaigns).

        Returns {"id": "campaign_group_id"} or None.
        """
        if not self.configured:
            log.warning("LinkedIn Ads not configured — skipping")
            return None

        return await self._post("campaignGroups", {
            "account": f"urn:li:sponsoredAccount:{self._account_id}",
            "name": name,
            "status": "ACTIVE",
        })

    async def create_campaign(
        self,
        name: str,
        campaign_type: str = "traffic",
        daily_budget_cents: int = 500,
        campaign_group_id: str | None = None,
        status: str = "paused",
    ) -> dict | None:
        """Create a LinkedIn campaign.

        Returns {"id": "campaign_id"} or None.
        """
        if not self.configured:
            log.warning("LinkedIn Ads not configured — skipping campaign creation")
            return None

        objective = OBJECTIVE_MAP.get(campaign_type, "WEBSITE_VISITS")
        li_status = STATUS_MAP.get(status, "PAUSED")

        body = {
            "account": f"urn:li:sponsoredAccount:{self._account_id}",
            "name": name,
            "objectiveType": objective,
            "status": li_status,
            "costType": "CPM",
            "dailyBudget": {
                "currencyCode": "USD",
                "amount": str(daily_budget_cents / 100),
            },
            "type": "SPONSORED_UPDATES",
            "unitCost": {
                "currencyCode": "USD",
                "amount": "0",  # Auto-bid
            },
        }
        if campaign_group_id:
            body["campaignGroup"] = f"urn:li:sponsoredCampaignGroup:{campaign_group_id}"

        result = await self._post("campaigns", body)
        if result and result.get("id"):
            log.info("Created LinkedIn campaign %s: %s", result["id"], name)
        return result

    async def create_creative(
        self,
        campaign_id: str,
        headline: str,
        body_text: str,
        cta: str | None = None,
        link_url: str | None = None,
    ) -> dict | None:
        """Create a sponsored content creative.

        Returns {"id": "creative_id"} or None.
        """
        if not self.configured:
            return None

        cta_label = self._resolve_cta(cta)

        creative_body = {
            "campaign": f"urn:li:sponsoredCampaign:{campaign_id}",
            "status": "ACTIVE",
            "type": "SPONSORED_STATUS_UPDATE",
            "reference": link_url or "https://arclane.cloud",
            "variables": {
                "data": {
                    "com.linkedin.ads.SponsoredUpdateCreativeVariables": {
                        "activity": "",
                        "directSponsoredContent": True,
                        "share": {
                            "shareCommentary": {"text": body_text},
                            "shareMediaCategory": "ARTICLE",
                            "media": [{
                                "title": {"text": headline},
                                "status": "READY",
                                "originalUrl": link_url or "https://arclane.cloud",
                                "description": {"text": body_text[:200]},
                                "landingPage": {
                                    "landingPageUrl": link_url or "https://arclane.cloud",
                                    "landingPageTitle": headline,
                                },
                            }],
                        },
                    }
                },
                "callToAction": {"labelType": cta_label},
            },
        }

        result = await self._post("creatives", creative_body)
        if result and result.get("id"):
            log.info("Created LinkedIn creative %s", result["id"])
        return result

    async def update_campaign_status(
        self, campaign_id: str, status: str
    ) -> dict | None:
        """Update campaign status (ACTIVE, PAUSED, ARCHIVED)."""
        if not self.configured:
            return None
        li_status = STATUS_MAP.get(status.lower(), status.upper())
        return await self._patch(
            f"campaigns/{campaign_id}",
            {"patch": {"$set": {"status": li_status}}},
        )

    # ------------------------------------------------------------------
    # Targeting
    # ------------------------------------------------------------------

    async def set_targeting(
        self,
        campaign_id: str,
        targeting: dict | None = None,
    ) -> dict | None:
        """Apply targeting criteria to a campaign.

        Converts Arclane segments to LinkedIn targeting facets.
        """
        if not self.configured or not targeting:
            return None

        targeting_criteria = self._build_targeting_criteria(targeting)
        return await self._patch(
            f"campaigns/{campaign_id}",
            {"patch": {"$set": {"targetingCriteria": targeting_criteria}}},
        )

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    async def get_campaign_analytics(
        self,
        campaign_id: str,
        date_range: str = "last_7_days",
    ) -> dict | None:
        """Fetch campaign analytics from LinkedIn.

        Returns normalized performance dict or None.
        """
        if not self.configured:
            return None

        result = await self._get(
            "adAnalytics",
            {
                "q": "analytics",
                "pivot": "CAMPAIGN",
                "campaigns": f"urn:li:sponsoredCampaign:{campaign_id}",
                "dateRange.start.year": "2026",
                "dateRange.start.month": "1",
                "dateRange.start.day": "1",
                "timeGranularity": "ALL",
                "fields": "impressions,clicks,costInLocalCurrency,externalWebsiteConversions",
            },
        )
        if not result or not result.get("elements"):
            return None

        # Aggregate elements
        total_impressions = 0
        total_clicks = 0
        total_cost = 0.0
        total_conversions = 0

        for elem in result["elements"]:
            total_impressions += int(elem.get("impressions", 0))
            total_clicks += int(elem.get("clicks", 0))
            total_cost += float(elem.get("costInLocalCurrency", 0))
            total_conversions += int(elem.get("externalWebsiteConversions", 0))

        spend_cents = int(total_cost * 100)
        cpc_cents = int(spend_cents / max(total_clicks, 1))
        ctr = (total_clicks / max(total_impressions, 1)) * 100

        return {
            "impressions": total_impressions,
            "clicks": total_clicks,
            "spend_cents": spend_cents,
            "cpc_cents": cpc_cents,
            "ctr": round(ctr, 2),
            "conversions": total_conversions,
        }

    # ------------------------------------------------------------------
    # Full Campaign Push
    # ------------------------------------------------------------------

    async def push_campaign(
        self,
        *,
        campaign_name: str,
        campaign_type: str,
        daily_budget_cents: int,
        ad_copies: list[dict],
        targeting: dict | None = None,
        link_url: str,
    ) -> dict | None:
        """End-to-end: create LinkedIn campaign + creatives + targeting.

        Returns dict with all created IDs, or None on failure.
        """
        if not self.configured:
            log.info("LinkedIn Ads not configured — skipping push")
            return None

        # Step 1: Create campaign
        campaign = await self.create_campaign(
            name=campaign_name,
            campaign_type=campaign_type,
            daily_budget_cents=daily_budget_cents,
            status="paused",
        )
        if not campaign or not campaign.get("id"):
            return None
        campaign_id = campaign["id"]

        # Step 2: Apply targeting
        if targeting:
            await self.set_targeting(campaign_id, targeting)

        # Step 3: Create creatives for each copy
        created_creatives = []
        for i, copy in enumerate(ad_copies):
            creative = await self.create_creative(
                campaign_id=campaign_id,
                headline=copy.get("headline", ""),
                body_text=copy.get("body", ""),
                cta=copy.get("cta"),
                link_url=link_url,
            )
            if creative and creative.get("id"):
                created_creatives.append({
                    "creative_id": creative["id"],
                    "headline": copy.get("headline"),
                })

        if not created_creatives:
            log.warning("No creatives created for LinkedIn campaign %s", campaign_id)
            return None

        # Step 4: Activate
        await self.update_campaign_status(campaign_id, "active")

        result = {
            "linkedin_campaign_id": campaign_id,
            "creatives": created_creatives,
            "total_creatives": len(created_creatives),
            "status": "ACTIVE",
        }
        log.info(
            "Pushed campaign to LinkedIn: %s (%d creatives)",
            campaign_id, len(created_creatives),
        )
        return result

    async def sync_campaign_performance(self, campaign_id: str) -> dict | None:
        """Fetch and normalize LinkedIn campaign metrics."""
        return await self.get_campaign_analytics(campaign_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_cta(cta_text: str | None) -> str:
        """Map CTA text to LinkedIn CTA label type."""
        if not cta_text:
            return "LEARN_MORE"
        mapping = {
            "learn more": "LEARN_MORE",
            "sign up": "SIGN_UP",
            "subscribe": "SUBSCRIBE",
            "register": "REGISTER",
            "apply now": "APPLY_NOW",
            "download": "DOWNLOAD",
            "get quote": "GET_QUOTE",
            "shop now": "LEARN_MORE",
            "try free": "SIGN_UP",
            "get started": "SIGN_UP",
            "see results": "LEARN_MORE",
            "start now": "SIGN_UP",
            "contact us": "LEARN_MORE",
        }
        return mapping.get(cta_text.lower().strip(), "LEARN_MORE")

    @staticmethod
    def _build_targeting_criteria(targeting: dict) -> dict:
        """Convert Arclane segment targeting to LinkedIn targeting criteria.

        LinkedIn supports: job titles, industries, company sizes, seniority,
        skills, interests, locations.
        """
        include = {"and": []}

        demographics = targeting.get("demographics") or {}
        psychographics = targeting.get("psychographics") or {}

        # Location (required by LinkedIn)
        location = demographics.get("location")
        if location:
            if isinstance(location, str):
                include["and"].append({
                    "or": {"urn:li:geo": [location]}
                })
            elif isinstance(location, list):
                include["and"].append({
                    "or": {"urn:li:geo": location}
                })

        # Interests from psychographics
        interests = psychographics.get("interests", [])
        if interests:
            include["and"].append({
                "or": {"urn:li:interest": interests[:10]}
            })

        # From segments
        for seg in targeting.get("segments", []):
            seg_psycho = seg.get("psychographics") or {}
            seg_interests = seg_psycho.get("interests", [])
            if seg_interests and not interests:
                include["and"].append({
                    "or": {"urn:li:interest": seg_interests[:10]}
                })
                break

        # If no targeting criteria built, add a broad default
        if not include["and"]:
            include["and"].append({
                "or": {"urn:li:geo": ["urn:li:geo:103644278"]}  # United States
            })

        return {"include": include}
