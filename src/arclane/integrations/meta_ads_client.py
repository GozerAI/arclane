"""Meta Marketing API client — creates and manages Facebook/Instagram ad campaigns.

Wraps the Meta Graph API v21.0 to:
  1. Create campaigns, ad sets, and ads on the business's ad account
  2. Upload ad creatives (headline, body, CTA, image)
  3. Sync performance metrics back to Arclane
  4. Pause/resume/delete campaigns

Requires:
  - ARCLANE_META_ADS_ACCESS_TOKEN  (system user or page access token)
  - ARCLANE_META_ADS_ACCOUNT_ID    (act_XXXXXXXXX)

Graceful degradation: all methods return None/empty on failure.
"""

import logging
from typing import Optional

import httpx

from arclane.core.config import settings

# Optional resilience
try:
    from gozerai_telemetry.resilience import (
        resilient_request,
        get_circuit_breaker,
        DEFAULT_RETRY,
    )
    _HAS_RESILIENCE = True
    _meta_cb = get_circuit_breaker("meta_ads", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _meta_cb = None

log = logging.getLogger("arclane.integrations.meta_ads")

META_GRAPH_URL = "https://graph.facebook.com/v21.0"
META_TIMEOUT = 15.0

# Map Arclane campaign types to Meta campaign objectives
OBJECTIVE_MAP = {
    "awareness": "OUTCOME_AWARENESS",
    "traffic": "OUTCOME_TRAFFIC",
    "conversion": "OUTCOME_SALES",
    "retargeting": "OUTCOME_SALES",
}

# Map Arclane campaign statuses to Meta statuses
STATUS_MAP = {
    "active": "ACTIVE",
    "paused": "PAUSED",
    "draft": "PAUSED",  # Create paused, activate on launch
}

# Map Arclane CTA strings to Meta CTA types
CTA_MAP = {
    "learn more": "LEARN_MORE",
    "shop now": "SHOP_NOW",
    "sign up": "SIGN_UP",
    "get started": "LEARN_MORE",
    "try free": "SIGN_UP",
    "see results": "LEARN_MORE",
    "start now": "SIGN_UP",
    "buy now": "SHOP_NOW",
    "contact us": "CONTACT_US",
    "download": "DOWNLOAD",
    "subscribe": "SUBSCRIBE",
}


def _resolve_cta(cta_text: str | None) -> str:
    """Map human-readable CTA to Meta enum. Default: LEARN_MORE."""
    if not cta_text:
        return "LEARN_MORE"
    return CTA_MAP.get(cta_text.lower().strip(), "LEARN_MORE")


class MetaAdsClient:
    """Async client for the Meta Marketing API (Facebook + Instagram ads).

    All public methods gracefully degrade: return None on failure, never raise.
    """

    def __init__(
        self,
        access_token: str | None = None,
        ad_account_id: str | None = None,
        base_url: str | None = None,
    ):
        self._token = access_token or settings.meta_ads_access_token
        self._account_id = ad_account_id or settings.meta_ads_account_id
        self._base_url = (base_url or META_GRAPH_URL).rstrip("/")

    @property
    def configured(self) -> bool:
        """True if credentials are set (token + account ID)."""
        return bool(self._token and self._account_id)

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict | None:
        """GET request to Graph API. Returns JSON dict or None."""
        full_params = {"access_token": self._token, **(params or {})}
        url = f"{self._base_url}/{path}"
        try:
            if _HAS_RESILIENCE:
                # Build query string into URL since resilient_request doesn't accept params
                qs = "&".join(f"{k}={v}" for k, v in full_params.items())
                full_url = f"{url}?{qs}" if qs else url
                return await resilient_request(
                    "GET", full_url,
                    timeout=META_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_meta_cb,
                )
            async with httpx.AsyncClient(timeout=META_TIMEOUT) as client:
                resp = await client.get(url, params=full_params)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("Meta GET %s failed: %s", path, exc)
            return None

    async def _post(self, path: str, data: dict) -> dict | None:
        """POST request to Graph API. Returns JSON dict or None."""
        url = f"{self._base_url}/{path}"
        payload = {"access_token": self._token, **data}
        try:
            if _HAS_RESILIENCE:
                return await resilient_request(
                    "POST", url, json_body=payload,
                    timeout=META_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_meta_cb,
                )
            async with httpx.AsyncClient(timeout=META_TIMEOUT) as client:
                resp = await client.post(url, data=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("Meta POST %s failed: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Campaign Management
    # ------------------------------------------------------------------

    async def create_campaign(
        self,
        name: str,
        campaign_type: str = "awareness",
        daily_budget_cents: int = 500,
        status: str = "paused",
    ) -> dict | None:
        """Create a Meta campaign under the configured ad account.

        Returns {"id": "campaign_id", ...} or None on failure.
        """
        if not self.configured:
            log.warning("Meta Ads not configured — skipping campaign creation")
            return None

        objective = OBJECTIVE_MAP.get(campaign_type, "OUTCOME_AWARENESS")
        meta_status = STATUS_MAP.get(status, "PAUSED")

        result = await self._post(
            f"{self._account_id}/campaigns",
            {
                "name": name,
                "objective": objective,
                "status": meta_status,
                "special_ad_categories": "[]",  # Required field
            },
        )
        if result and "id" in result:
            log.info("Created Meta campaign %s: %s", result["id"], name)
        return result

    async def create_ad_set(
        self,
        campaign_id: str,
        name: str,
        daily_budget_cents: int = 500,
        targeting: dict | None = None,
        *,
        optimization_goal: str = "LINK_CLICKS",
        billing_event: str = "IMPRESSIONS",
        bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
        start_time: str | None = None,
        end_time: str | None = None,
        platforms: list[str] | None = None,
    ) -> dict | None:
        """Create an ad set within a Meta campaign.

        Returns {"id": "adset_id"} or None on failure.
        """
        if not self.configured:
            return None

        # Build Meta targeting spec from Arclane segments
        targeting_spec = self._build_targeting_spec(targeting or {})

        # Platform placement
        publisher_platforms = platforms or ["facebook", "instagram"]

        data = {
            "name": name,
            "campaign_id": campaign_id,
            "daily_budget": str(daily_budget_cents),  # Meta uses cents
            "optimization_goal": optimization_goal,
            "billing_event": billing_event,
            "bid_strategy": bid_strategy,
            "targeting": str(targeting_spec),  # JSON-encoded targeting spec
            "publisher_platforms": str(publisher_platforms),
            "status": "PAUSED",
        }
        if start_time:
            data["start_time"] = start_time
        if end_time:
            data["end_time"] = end_time

        result = await self._post(f"{self._account_id}/adsets", data)
        if result and "id" in result:
            log.info("Created Meta ad set %s for campaign %s", result["id"], campaign_id)
        return result

    async def create_ad_creative(
        self,
        name: str,
        headline: str,
        body: str,
        cta: str | None = None,
        link_url: str | None = None,
        image_url: str | None = None,
        page_id: str | None = None,
    ) -> dict | None:
        """Create an ad creative (the actual ad content).

        Returns {"id": "creative_id"} or None on failure.
        """
        if not self.configured:
            return None

        # Build object_story_spec for link ads
        link_data = {
            "message": body,
            "name": headline,
            "call_to_action": {
                "type": _resolve_cta(cta),
                "value": {"link": link_url or "https://arclane.cloud"},
            },
        }
        if image_url:
            link_data["picture"] = image_url

        # page_id is required for ad creatives
        effective_page_id = page_id or settings.meta_ads_page_id

        data = {
            "name": name,
            "object_story_spec": str({
                "page_id": effective_page_id,
                "link_data": link_data,
            }),
        }

        result = await self._post(f"{self._account_id}/adcreatives", data)
        if result and "id" in result:
            log.info("Created Meta creative %s", result["id"])
        return result

    async def create_ad(
        self,
        ad_set_id: str,
        creative_id: str,
        name: str,
        status: str = "PAUSED",
    ) -> dict | None:
        """Create an ad linking an ad set to a creative.

        Returns {"id": "ad_id"} or None on failure.
        """
        if not self.configured:
            return None

        result = await self._post(
            f"{self._account_id}/ads",
            {
                "name": name,
                "adset_id": ad_set_id,
                "creative": str({"creative_id": creative_id}),
                "status": status,
            },
        )
        if result and "id" in result:
            log.info("Created Meta ad %s in ad set %s", result["id"], ad_set_id)
        return result

    async def update_campaign_status(
        self, campaign_id: str, status: str
    ) -> dict | None:
        """Update a Meta campaign's status (ACTIVE, PAUSED, DELETED)."""
        if not self.configured:
            return None
        return await self._post(campaign_id, {"status": status})

    # ------------------------------------------------------------------
    # Performance & Insights
    # ------------------------------------------------------------------

    async def get_campaign_insights(
        self,
        campaign_id: str,
        fields: str = "impressions,clicks,spend,actions,cpc,ctr,cpp",
        date_preset: str = "last_7d",
    ) -> dict | None:
        """Fetch performance insights for a Meta campaign.

        Returns {"data": [...], "paging": {...}} or None.
        """
        if not self.configured:
            return None
        return await self._get(
            f"{campaign_id}/insights",
            {"fields": fields, "date_preset": date_preset},
        )

    async def get_ad_insights(
        self,
        ad_id: str,
        fields: str = "impressions,clicks,spend,actions,cpc,ctr",
        date_preset: str = "last_7d",
    ) -> dict | None:
        """Fetch performance insights for a single ad."""
        if not self.configured:
            return None
        return await self._get(
            f"{ad_id}/insights",
            {"fields": fields, "date_preset": date_preset},
        )

    # ------------------------------------------------------------------
    # Full Campaign Push (orchestrator-level)
    # ------------------------------------------------------------------

    async def push_campaign(
        self,
        *,
        campaign_name: str,
        campaign_type: str,
        daily_budget_cents: int,
        ad_copies: list[dict],
        targeting: dict | None = None,
        link_url: str | None = None,
        page_id: str | None = None,
        platforms: list[str] | None = None,
    ) -> dict | None:
        """End-to-end: create Meta campaign + ad set + creatives + ads.

        This is the main entry point used by advertising_service.launch_campaign().

        Args:
            campaign_name: Campaign display name
            campaign_type: awareness|traffic|conversion|retargeting
            daily_budget_cents: Daily budget in cents (minimum 500 = $5)
            ad_copies: List of dicts with headline, body, cta keys
            targeting: Arclane segment targeting dict
            link_url: Destination URL for ad clicks
            page_id: Facebook Page ID (falls back to settings)
            platforms: ["facebook"], ["instagram"], or both

        Returns dict with all created IDs, or None on any failure.
        """
        if not self.configured:
            log.info("Meta Ads not configured — skipping push")
            return None

        # Step 1: Create campaign
        campaign = await self.create_campaign(
            name=campaign_name,
            campaign_type=campaign_type,
            daily_budget_cents=daily_budget_cents,
            status="paused",
        )
        if not campaign or "id" not in campaign:
            return None
        meta_campaign_id = campaign["id"]

        # Step 2: Create ad set with targeting
        ad_set = await self.create_ad_set(
            campaign_id=meta_campaign_id,
            name=f"{campaign_name} — Ad Set",
            daily_budget_cents=daily_budget_cents,
            targeting=targeting,
            platforms=platforms,
        )
        if not ad_set or "id" not in ad_set:
            return None
        meta_ad_set_id = ad_set["id"]

        # Step 3: Create creatives and ads for each copy variation
        created_ads = []
        for i, copy in enumerate(ad_copies):
            creative = await self.create_ad_creative(
                name=f"{campaign_name} — Creative {i + 1}",
                headline=copy.get("headline", ""),
                body=copy.get("body", ""),
                cta=copy.get("cta"),
                link_url=link_url,
                page_id=page_id,
            )
            if not creative or "id" not in creative:
                continue

            ad = await self.create_ad(
                ad_set_id=meta_ad_set_id,
                creative_id=creative["id"],
                name=f"{campaign_name} — Ad {i + 1}",
                status="PAUSED",
            )
            if ad and "id" in ad:
                created_ads.append({
                    "ad_id": ad["id"],
                    "creative_id": creative["id"],
                    "headline": copy.get("headline"),
                })

        if not created_ads:
            log.warning("No ads were created for campaign %s", meta_campaign_id)
            return None

        # Step 4: Activate everything
        await self.update_campaign_status(meta_campaign_id, "ACTIVE")

        result = {
            "meta_campaign_id": meta_campaign_id,
            "meta_ad_set_id": meta_ad_set_id,
            "ads": created_ads,
            "total_ads": len(created_ads),
            "status": "ACTIVE",
        }
        log.info(
            "Pushed campaign to Meta: %s (%d ads)",
            meta_campaign_id, len(created_ads),
        )
        return result

    async def sync_campaign_performance(self, meta_campaign_id: str) -> dict | None:
        """Fetch and normalize Meta campaign insights into Arclane format.

        Returns dict with impressions, clicks, spend_cents, cpc_cents, ctr, conversions.
        """
        raw = await self.get_campaign_insights(meta_campaign_id)
        if not raw or not raw.get("data"):
            return None

        entry = raw["data"][0] if raw["data"] else {}
        impressions = int(entry.get("impressions", 0))
        clicks = int(entry.get("clicks", 0))
        spend = entry.get("spend", "0")
        cpc = entry.get("cpc", "0")
        ctr = entry.get("ctr", "0")

        # Extract conversions from actions
        conversions = 0
        for action in entry.get("actions", []):
            if action.get("action_type") in (
                "offsite_conversion",
                "lead",
                "purchase",
                "complete_registration",
            ):
                conversions += int(action.get("value", 0))

        return {
            "impressions": impressions,
            "clicks": clicks,
            "spend_cents": int(float(spend) * 100),
            "cpc_cents": int(float(cpc) * 100),
            "ctr": float(ctr),
            "conversions": conversions,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_targeting_spec(targeting: dict) -> dict:
        """Convert Arclane segment targeting to Meta targeting spec format.

        Arclane segments store:
          demographics: {age_range, gender, location, income_level}
          psychographics: {interests, values, pain_points}
          behaviors: {online_habits, buying_patterns}

        Meta targeting spec uses:
          age_min, age_max, genders, geo_locations, interests, behaviors
        """
        spec: dict = {}

        # Demographics
        demographics = targeting.get("demographics") or {}
        age_range = demographics.get("age_range", "")
        if age_range and "-" in str(age_range):
            parts = str(age_range).split("-")
            try:
                spec["age_min"] = int(parts[0])
                spec["age_max"] = int(parts[1])
            except (ValueError, IndexError):
                pass

        gender = demographics.get("gender", "")
        if gender:
            gender_map = {"male": [1], "female": [2], "all": [1, 2]}
            spec["genders"] = gender_map.get(gender.lower(), [1, 2])

        location = demographics.get("location")
        if location:
            # Support country codes or city names
            if isinstance(location, str) and len(location) == 2:
                spec["geo_locations"] = {"countries": [location.upper()]}
            elif isinstance(location, list):
                spec["geo_locations"] = {"countries": [loc.upper() for loc in location if len(loc) == 2]}

        # Psychographics → Meta interests
        psychographics = targeting.get("psychographics") or {}
        interests = psychographics.get("interests", [])
        if interests:
            # Meta interests need {id, name} pairs — use names as search hints
            spec["flexible_spec"] = [{"interests": [{"name": i} for i in interests[:10]]}]

        # Behaviors
        behaviors = targeting.get("behaviors") or {}
        buying_patterns = behaviors.get("buying_patterns", [])
        if buying_patterns:
            if "flexible_spec" not in spec:
                spec["flexible_spec"] = [{}]
            spec["flexible_spec"][0]["behaviors"] = [{"name": b} for b in buying_patterns[:10]]

        # Segments from Arclane targeting
        segments = targeting.get("segments", [])
        for seg in segments:
            seg_demo = seg.get("demographics") or {}
            seg_psycho = seg.get("psychographics") or {}
            seg_interests = seg_psycho.get("interests", [])
            if seg_interests and "flexible_spec" not in spec:
                spec["flexible_spec"] = [{"interests": [{"name": i} for i in seg_interests[:10]]}]

        return spec
