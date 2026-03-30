"""Google Ads REST API client — creates and manages Google search/display ad campaigns.

Wraps the Google Ads REST API v17 to:
  1. Create campaigns, ad groups, and ads
  2. Configure keyword targeting and audience segments
  3. Sync performance metrics back to Arclane
  4. Pause/resume campaigns

Requires:
  - ARCLANE_GOOGLE_ADS_DEVELOPER_TOKEN  (from Google Ads API Center)
  - ARCLANE_GOOGLE_ADS_CUSTOMER_ID      (xxx-xxx-xxxx, no dashes in API calls)
  - ARCLANE_GOOGLE_ADS_REFRESH_TOKEN    (OAuth2 refresh token)
  - ARCLANE_GOOGLE_CLIENT_ID            (already exists in config)
  - ARCLANE_GOOGLE_CLIENT_SECRET        (already exists in config)

Authentication flow:
  1. Use refresh token + client ID/secret to obtain short-lived access token
  2. Pass access token + developer token in headers to the REST API

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
    _gads_cb = get_circuit_breaker("google_ads", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _gads_cb = None

log = logging.getLogger("arclane.integrations.google_ads")

GOOGLE_ADS_BASE_URL = "https://googleads.googleapis.com/v17"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ADS_TIMEOUT = 15.0

# Map Arclane campaign types to Google Ads campaign types + bidding
CAMPAIGN_TYPE_MAP = {
    "awareness": {
        "advertising_channel_type": "DISPLAY",
        "bidding_strategy_type": "TARGET_IMPRESSION_SHARE",
    },
    "traffic": {
        "advertising_channel_type": "SEARCH",
        "bidding_strategy_type": "MAXIMIZE_CLICKS",
    },
    "conversion": {
        "advertising_channel_type": "SEARCH",
        "bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
    },
    "retargeting": {
        "advertising_channel_type": "DISPLAY",
        "bidding_strategy_type": "TARGET_CPA",
    },
}

# Map Arclane campaign statuses to Google Ads statuses
STATUS_MAP = {
    "active": "ENABLED",
    "paused": "PAUSED",
    "draft": "PAUSED",
}


class GoogleAdsClient:
    """Async client for the Google Ads REST API.

    All public methods gracefully degrade: return None on failure, never raise.
    """

    def __init__(
        self,
        developer_token: str | None = None,
        customer_id: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
    ):
        self._developer_token = developer_token or settings.google_ads_developer_token
        # Strip dashes from customer ID (API expects plain digits)
        raw_id = customer_id or settings.google_ads_customer_id
        self._customer_id = raw_id.replace("-", "")
        self._refresh_token = refresh_token or settings.google_ads_refresh_token
        self._client_id = client_id or settings.google_client_id
        self._client_secret = client_secret or settings.google_client_secret
        self._base_url = (base_url or GOOGLE_ADS_BASE_URL).rstrip("/")

        # Cached access token
        self._access_token: str | None = None

    @property
    def configured(self) -> bool:
        """True if all required credentials are set."""
        return bool(
            self._developer_token
            and self._customer_id
            and self._refresh_token
            and self._client_id
            and self._client_secret
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _ensure_access_token(self) -> str | None:
        """Exchange refresh token for a short-lived access token."""
        if self._access_token:
            return self._access_token
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self._access_token = data.get("access_token")
                return self._access_token
        except Exception as exc:
            log.warning("Failed to refresh Google Ads access token: %s", exc)
            return None

    def _headers(self, access_token: str) -> dict:
        return {
            "Authorization": f"Bearer {access_token}",
            "developer-token": self._developer_token,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _post(self, path: str, body: dict) -> dict | None:
        """POST to Google Ads REST API. Returns JSON dict or None."""
        access_token = await self._ensure_access_token()
        if not access_token:
            return None

        url = f"{self._base_url}/{path}"
        headers = self._headers(access_token)
        try:
            if _HAS_RESILIENCE:
                return await resilient_request(
                    "POST", url, json_body=body, headers=headers,
                    timeout=GOOGLE_ADS_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_gads_cb,
                )
            async with httpx.AsyncClient(timeout=GOOGLE_ADS_TIMEOUT) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("Google Ads POST %s failed: %s", path, exc)
            return None

    async def _search(self, query: str) -> list[dict]:
        """Execute a GAQL query via the searchStream endpoint."""
        access_token = await self._ensure_access_token()
        if not access_token:
            return []

        url = f"{self._base_url}/customers/{self._customer_id}/googleAds:searchStream"
        headers = self._headers(access_token)
        body = {"query": query}
        try:
            if _HAS_RESILIENCE:
                result = await resilient_request(
                    "POST", url, json_body=body, headers=headers,
                    timeout=GOOGLE_ADS_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_gads_cb,
                )
            else:
                async with httpx.AsyncClient(timeout=GOOGLE_ADS_TIMEOUT) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    result = resp.json()

            if not result:
                return []
            # searchStream returns a list of batches
            rows = []
            batches = result if isinstance(result, list) else [result]
            for batch in batches:
                rows.extend(batch.get("results", []))
            return rows
        except Exception as exc:
            log.warning("Google Ads search failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Resource name helpers
    # ------------------------------------------------------------------

    def _customer_resource(self) -> str:
        return f"customers/{self._customer_id}"

    def _campaign_resource(self, campaign_id: str) -> str:
        return f"customers/{self._customer_id}/campaigns/{campaign_id}"

    def _ad_group_resource(self, ad_group_id: str) -> str:
        return f"customers/{self._customer_id}/adGroups/{ad_group_id}"

    # ------------------------------------------------------------------
    # Campaign Management
    # ------------------------------------------------------------------

    async def create_campaign(
        self,
        name: str,
        campaign_type: str = "traffic",
        daily_budget_cents: int = 500,
        status: str = "paused",
    ) -> dict | None:
        """Create a Google Ads campaign with a budget.

        Returns {"campaign_resource_name": "...", "budget_resource_name": "..."} or None.
        """
        if not self.configured:
            log.warning("Google Ads not configured — skipping campaign creation")
            return None

        type_config = CAMPAIGN_TYPE_MAP.get(campaign_type, CAMPAIGN_TYPE_MAP["traffic"])
        google_status = STATUS_MAP.get(status, "PAUSED")

        # Step 1: Create campaign budget
        budget_result = await self._post(
            f"customers/{self._customer_id}/campaignBudgets:mutate",
            {
                "operations": [{
                    "create": {
                        "name": f"{name} Budget",
                        "amountMicros": str(daily_budget_cents * 10_000),  # cents → micros
                        "deliveryMethod": "STANDARD",
                    }
                }],
            },
        )
        if not budget_result or "results" not in budget_result:
            return None
        budget_rn = budget_result["results"][0].get("resourceName", "")

        # Step 2: Create campaign
        campaign_result = await self._post(
            f"customers/{self._customer_id}/campaigns:mutate",
            {
                "operations": [{
                    "create": {
                        "name": name,
                        "status": google_status,
                        "advertisingChannelType": type_config["advertising_channel_type"],
                        "campaignBudget": budget_rn,
                        type_config["bidding_strategy_type"].lower(): {},
                    }
                }],
            },
        )
        if not campaign_result or "results" not in campaign_result:
            return None

        campaign_rn = campaign_result["results"][0].get("resourceName", "")
        log.info("Created Google Ads campaign: %s", campaign_rn)

        return {
            "campaign_resource_name": campaign_rn,
            "budget_resource_name": budget_rn,
        }

    async def create_ad_group(
        self,
        campaign_resource_name: str,
        name: str,
        cpc_bid_micros: int = 1_000_000,  # $1.00 default bid
    ) -> dict | None:
        """Create an ad group within a campaign.

        Returns {"ad_group_resource_name": "..."} or None.
        """
        if not self.configured:
            return None

        result = await self._post(
            f"customers/{self._customer_id}/adGroups:mutate",
            {
                "operations": [{
                    "create": {
                        "name": name,
                        "campaign": campaign_resource_name,
                        "status": "ENABLED",
                        "type": "SEARCH_STANDARD",
                        "cpcBidMicros": str(cpc_bid_micros),
                    }
                }],
            },
        )
        if not result or "results" not in result:
            return None

        ad_group_rn = result["results"][0].get("resourceName", "")
        log.info("Created Google Ads ad group: %s", ad_group_rn)
        return {"ad_group_resource_name": ad_group_rn}

    async def create_responsive_search_ad(
        self,
        ad_group_resource_name: str,
        headlines: list[str],
        descriptions: list[str],
        final_url: str,
    ) -> dict | None:
        """Create a responsive search ad (Google's primary ad format).

        Args:
            headlines: Up to 15 headlines (30 chars each). Min 3.
            descriptions: Up to 4 descriptions (90 chars each). Min 2.
            final_url: Landing page URL.

        Returns {"ad_resource_name": "..."} or None.
        """
        if not self.configured:
            return None

        # Enforce Google Ads limits
        headline_assets = [
            {"text": h[:30]} for h in headlines[:15]
        ]
        description_assets = [
            {"text": d[:90]} for d in descriptions[:4]
        ]

        # Pad to minimums
        while len(headline_assets) < 3:
            headline_assets.append({"text": "Learn More Today"})
        while len(description_assets) < 2:
            description_assets.append({"text": "Visit our website to learn more."})

        result = await self._post(
            f"customers/{self._customer_id}/adGroupAds:mutate",
            {
                "operations": [{
                    "create": {
                        "adGroup": ad_group_resource_name,
                        "status": "ENABLED",
                        "ad": {
                            "responsiveSearchAd": {
                                "headlines": headline_assets,
                                "descriptions": description_assets,
                            },
                            "finalUrls": [final_url],
                        },
                    }
                }],
            },
        )
        if not result or "results" not in result:
            return None

        ad_rn = result["results"][0].get("resourceName", "")
        log.info("Created Google Ads responsive search ad: %s", ad_rn)
        return {"ad_resource_name": ad_rn}

    async def add_keywords(
        self,
        ad_group_resource_name: str,
        keywords: list[str],
        match_type: str = "BROAD",
    ) -> list[dict]:
        """Add keyword criteria to an ad group.

        Returns list of created keyword resource names.
        """
        if not self.configured or not keywords:
            return []

        operations = [
            {
                "create": {
                    "adGroup": ad_group_resource_name,
                    "status": "ENABLED",
                    "keyword": {
                        "text": kw[:80],
                        "matchType": match_type,
                    },
                }
            }
            for kw in keywords[:20]  # Max 20 keywords per batch
        ]

        result = await self._post(
            f"customers/{self._customer_id}/adGroupCriteria:mutate",
            {"operations": operations},
        )
        if not result or "results" not in result:
            return []

        return [
            {"keyword_resource_name": r.get("resourceName", "")}
            for r in result["results"]
        ]

    async def update_campaign_status(
        self, campaign_resource_name: str, status: str
    ) -> dict | None:
        """Update a campaign's status (ENABLED, PAUSED, REMOVED)."""
        if not self.configured:
            return None

        google_status = {"active": "ENABLED", "paused": "PAUSED", "removed": "REMOVED"}.get(
            status.lower(), status.upper()
        )

        return await self._post(
            f"customers/{self._customer_id}/campaigns:mutate",
            {
                "operations": [{
                    "update": {
                        "resourceName": campaign_resource_name,
                        "status": google_status,
                    },
                    "updateMask": "status",
                }],
            },
        )

    # ------------------------------------------------------------------
    # Performance & Insights
    # ------------------------------------------------------------------

    async def get_campaign_performance(
        self,
        campaign_resource_name: str,
        date_range: str = "LAST_7_DAYS",
    ) -> dict | None:
        """Fetch campaign performance metrics via GAQL.

        Returns normalized dict or None.
        """
        if not self.configured:
            return None

        # Extract campaign ID from resource name
        campaign_id = campaign_resource_name.rsplit("/", 1)[-1]

        query = f"""
            SELECT
                campaign.id,
                campaign.name,
                metrics.impressions,
                metrics.clicks,
                metrics.cost_micros,
                metrics.conversions,
                metrics.average_cpc,
                metrics.ctr
            FROM campaign
            WHERE campaign.id = {campaign_id}
                AND segments.date DURING {date_range}
        """

        rows = await self._search(query)
        if not rows:
            return None

        # Aggregate across date segments
        total_impressions = 0
        total_clicks = 0
        total_cost_micros = 0
        total_conversions = 0.0

        for row in rows:
            metrics = row.get("metrics", {})
            total_impressions += int(metrics.get("impressions", 0))
            total_clicks += int(metrics.get("clicks", 0))
            total_cost_micros += int(metrics.get("costMicros", 0))
            total_conversions += float(metrics.get("conversions", 0))

        spend_cents = total_cost_micros // 10_000  # micros → cents
        cpc_cents = (total_cost_micros // max(total_clicks, 1)) // 10_000 if total_clicks else 0
        ctr = (total_clicks / max(total_impressions, 1)) * 100 if total_impressions else 0.0

        return {
            "impressions": total_impressions,
            "clicks": total_clicks,
            "spend_cents": spend_cents,
            "cpc_cents": cpc_cents,
            "ctr": round(ctr, 2),
            "conversions": int(total_conversions),
        }

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
        link_url: str,
    ) -> dict | None:
        """End-to-end: create Google Ads campaign + ad group + ads + keywords.

        Args:
            campaign_name: Campaign display name
            campaign_type: awareness|traffic|conversion|retargeting
            daily_budget_cents: Daily budget in cents (minimum 500 = $5)
            ad_copies: List of dicts with headline, body, cta keys
            targeting: Arclane segment targeting dict (used for keyword extraction)
            link_url: Destination URL for ad clicks

        Returns dict with all created resource names, or None on failure.
        """
        if not self.configured:
            log.info("Google Ads not configured — skipping push")
            return None

        # Step 1: Create campaign with budget
        campaign = await self.create_campaign(
            name=campaign_name,
            campaign_type=campaign_type,
            daily_budget_cents=daily_budget_cents,
            status="paused",
        )
        if not campaign:
            return None
        campaign_rn = campaign["campaign_resource_name"]

        # Step 2: Create ad group
        ad_group = await self.create_ad_group(
            campaign_resource_name=campaign_rn,
            name=f"{campaign_name} — Ad Group",
        )
        if not ad_group:
            return None
        ad_group_rn = ad_group["ad_group_resource_name"]

        # Step 3: Create responsive search ads from copies
        # Collect all headlines and descriptions across copies
        all_headlines = []
        all_descriptions = []
        for copy in ad_copies:
            headline = copy.get("headline", "")
            body = copy.get("body", "")
            cta = copy.get("cta", "")
            if headline:
                all_headlines.append(headline)
            if cta and cta not in all_headlines:
                all_headlines.append(cta)
            if body:
                all_descriptions.append(body)

        ad_result = await self.create_responsive_search_ad(
            ad_group_resource_name=ad_group_rn,
            headlines=all_headlines,
            descriptions=all_descriptions,
            final_url=link_url,
        )

        # Step 4: Add keywords from targeting
        keywords = self._extract_keywords(targeting or {}, ad_copies)
        keyword_results = []
        if keywords:
            keyword_results = await self.add_keywords(ad_group_rn, keywords)

        # Step 5: Activate
        await self.update_campaign_status(campaign_rn, "active")

        result = {
            "google_campaign_resource_name": campaign_rn,
            "google_budget_resource_name": campaign["budget_resource_name"],
            "google_ad_group_resource_name": ad_group_rn,
            "google_ad_resource_name": ad_result.get("ad_resource_name") if ad_result else None,
            "keywords_added": len(keyword_results),
            "total_headlines": len(all_headlines),
            "total_descriptions": len(all_descriptions),
            "status": "ENABLED",
        }
        log.info(
            "Pushed campaign to Google Ads: %s (%d headlines, %d keywords)",
            campaign_rn, len(all_headlines), len(keyword_results),
        )
        return result

    async def sync_campaign_performance(self, campaign_resource_name: str) -> dict | None:
        """Fetch and normalize Google Ads campaign metrics into Arclane format."""
        return await self.get_campaign_performance(campaign_resource_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(targeting: dict, ad_copies: list[dict]) -> list[str]:
        """Extract keyword candidates from targeting data and ad copy.

        Pulls interests from psychographics and key terms from ad headlines/bodies.
        """
        keywords = []

        # From targeting psychographics
        segments = targeting.get("segments", [])
        for seg in segments:
            psycho = seg.get("psychographics") or {}
            keywords.extend(psycho.get("interests", []))
            keywords.extend(psycho.get("pain_points", []))

        psycho = targeting.get("psychographics") or {}
        keywords.extend(psycho.get("interests", []))
        keywords.extend(psycho.get("pain_points", []))

        # From ad copy headlines (split into potential keywords)
        for copy in ad_copies:
            headline = copy.get("headline", "")
            if headline:
                # Use the full headline as a phrase match candidate
                keywords.append(headline)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if kw_lower and kw_lower not in seen:
                seen.add(kw_lower)
                unique.append(kw)

        return unique[:20]
