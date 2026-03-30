"""Twitter/X Ads API client — creates and manages Twitter ad campaigns.

Wraps the Twitter Ads API v12 to:
  1. Create campaigns, line items (ad groups), and promoted tweets
  2. Target by interests, keywords, follower lookalikes, locations
  3. Sync performance metrics back to Arclane
  4. Pause/resume campaigns

Requires:
  - ARCLANE_TWITTER_ADS_ACCOUNT_ID      (Twitter Ads account ID)
  - ARCLANE_TWITTER_ADS_CONSUMER_KEY    (API key)
  - ARCLANE_TWITTER_ADS_CONSUMER_SECRET (API secret)
  - ARCLANE_TWITTER_ADS_ACCESS_TOKEN    (OAuth1.0a access token)
  - ARCLANE_TWITTER_ADS_ACCESS_SECRET   (OAuth1.0a access token secret)

Graceful degradation: all methods return None/empty on failure.
"""

import hashlib
import hmac
import logging
import time
import urllib.parse
import uuid

import httpx

from arclane.core.config import settings

try:
    from gozerai_telemetry.resilience import (
        resilient_request,
        get_circuit_breaker,
        DEFAULT_RETRY,
    )
    _HAS_RESILIENCE = True
    _tw_cb = get_circuit_breaker("twitter_ads", failure_threshold=3, recovery_timeout=120)
except ImportError:
    _HAS_RESILIENCE = False
    _tw_cb = None

log = logging.getLogger("arclane.integrations.twitter_ads")

TWITTER_ADS_BASE_URL = "https://ads-api.x.com/12"
TWITTER_TIMEOUT = 15.0

# Map Arclane campaign types to Twitter objectives
OBJECTIVE_MAP = {
    "awareness": "REACH",
    "traffic": "WEBSITE_CLICKS",
    "conversion": "WEBSITE_CONVERSIONS",
    "retargeting": "WEBSITE_CONVERSIONS",
}

STATUS_MAP = {
    "active": "ACTIVE",
    "paused": "PAUSED",
    "draft": "PAUSED",
}

# Twitter line item placements
PLACEMENT_MAP = {
    "awareness": "ALL_ON_TWITTER",
    "traffic": "ALL_ON_TWITTER",
    "conversion": "ALL_ON_TWITTER",
    "retargeting": "ALL_ON_TWITTER",
}


def _oauth1_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    access_token: str,
    access_secret: str,
    body_params: dict | None = None,
) -> str:
    """Generate OAuth 1.0a Authorization header."""
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }

    # Combine all params for signature base
    all_params = {**oauth_params, **(body_params or {})}
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(all_params.items())
    )

    base_string = f"{method.upper()}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    signing_key = f"{urllib.parse.quote(consumer_secret, safe='')}&{urllib.parse.quote(access_secret, safe='')}"

    import base64
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()

    oauth_params["oauth_signature"] = signature
    header_parts = ", ".join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_parts}"


class TwitterAdsClient:
    """Async client for the Twitter/X Ads API.

    All public methods gracefully degrade: return None on failure, never raise.
    """

    def __init__(
        self,
        account_id: str | None = None,
        consumer_key: str | None = None,
        consumer_secret: str | None = None,
        access_token: str | None = None,
        access_secret: str | None = None,
        base_url: str | None = None,
    ):
        self._account_id = account_id or settings.twitter_ads_account_id
        self._consumer_key = consumer_key or settings.twitter_ads_consumer_key
        self._consumer_secret = consumer_secret or settings.twitter_ads_consumer_secret
        self._access_token = access_token or settings.twitter_ads_access_token
        self._access_secret = access_secret or settings.twitter_ads_access_secret
        self._base_url = (base_url or TWITTER_ADS_BASE_URL).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(
            self._account_id
            and self._consumer_key
            and self._consumer_secret
            and self._access_token
            and self._access_secret
        )

    def _auth_header(self, method: str, url: str, params: dict | None = None) -> str:
        return _oauth1_header(
            method, url,
            self._consumer_key, self._consumer_secret,
            self._access_token, self._access_secret,
            params,
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict | None:
        url = f"{self._base_url}/{path}"
        auth = self._auth_header("GET", url, params)
        headers = {"Authorization": auth, "Content-Type": "application/json"}
        try:
            if _HAS_RESILIENCE:
                qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
                full_url = f"{url}?{qs}" if qs else url
                return await resilient_request(
                    "GET", full_url, headers=headers,
                    timeout=TWITTER_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_tw_cb,
                )
            async with httpx.AsyncClient(timeout=TWITTER_TIMEOUT) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("Twitter GET %s failed: %s", path, exc)
            return None

    async def _post(self, path: str, body: dict) -> dict | None:
        url = f"{self._base_url}/{path}"
        auth = self._auth_header("POST", url)
        headers = {"Authorization": auth, "Content-Type": "application/json"}
        try:
            if _HAS_RESILIENCE:
                return await resilient_request(
                    "POST", url, json_body=body, headers=headers,
                    timeout=TWITTER_TIMEOUT, retry_policy=DEFAULT_RETRY,
                    circuit_breaker=_tw_cb,
                )
            async with httpx.AsyncClient(timeout=TWITTER_TIMEOUT) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("Twitter POST %s failed: %s", path, exc)
            return None

    async def _put(self, path: str, body: dict) -> dict | None:
        url = f"{self._base_url}/{path}"
        auth = self._auth_header("PUT", url)
        headers = {"Authorization": auth, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=TWITTER_TIMEOUT) as client:
                resp = await client.put(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("Twitter PUT %s failed: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Funding Instrument (required for campaigns)
    # ------------------------------------------------------------------

    async def get_funding_instrument(self) -> str | None:
        """Get the first active funding instrument for the account.

        Returns funding_instrument_id or None.
        """
        if not self.configured:
            return None
        result = await self._get(f"accounts/{self._account_id}/funding_instruments")
        if not result:
            return None
        data = result.get("data", [])
        for fi in data:
            if fi.get("entity_status") == "ACTIVE":
                return fi.get("id")
        return data[0].get("id") if data else None

    # ------------------------------------------------------------------
    # Campaign Management
    # ------------------------------------------------------------------

    async def create_campaign(
        self,
        name: str,
        campaign_type: str = "traffic",
        daily_budget_cents: int = 500,
        funding_instrument_id: str | None = None,
        status: str = "paused",
    ) -> dict | None:
        """Create a Twitter Ads campaign.

        Returns {"id": "campaign_id", ...} or None.
        """
        if not self.configured:
            log.warning("Twitter Ads not configured — skipping campaign creation")
            return None

        # Get funding instrument if not provided
        if not funding_instrument_id:
            funding_instrument_id = await self.get_funding_instrument()
            if not funding_instrument_id:
                log.warning("No funding instrument found for Twitter Ads account")
                return None

        tw_status = STATUS_MAP.get(status, "PAUSED")

        result = await self._post(
            f"accounts/{self._account_id}/campaigns",
            {
                "name": name,
                "funding_instrument_id": funding_instrument_id,
                "daily_budget_amount_local_micro": daily_budget_cents * 10_000,
                "entity_status": tw_status,
                "start_time": None,  # Start immediately when activated
            },
        )
        if not result:
            return None

        data = result.get("data", result)
        campaign_id = data.get("id")
        if campaign_id:
            log.info("Created Twitter campaign %s: %s", campaign_id, name)
        return {"id": campaign_id} if campaign_id else None

    async def create_line_item(
        self,
        campaign_id: str,
        name: str,
        campaign_type: str = "traffic",
        bid_amount_cents: int = 100,
    ) -> dict | None:
        """Create a line item (ad group) within a campaign.

        Returns {"id": "line_item_id"} or None.
        """
        if not self.configured:
            return None

        objective = OBJECTIVE_MAP.get(campaign_type, "WEBSITE_CLICKS")
        placement = PLACEMENT_MAP.get(campaign_type, "ALL_ON_TWITTER")

        result = await self._post(
            f"accounts/{self._account_id}/line_items",
            {
                "campaign_id": campaign_id,
                "name": name,
                "objective": objective,
                "placements": [placement],
                "bid_amount_local_micro": bid_amount_cents * 10_000,
                "product_type": "PROMOTED_TWEETS",
                "entity_status": "ACTIVE",
            },
        )
        if not result:
            return None

        data = result.get("data", result)
        line_item_id = data.get("id")
        if line_item_id:
            log.info("Created Twitter line item %s", line_item_id)
        return {"id": line_item_id} if line_item_id else None

    async def create_promoted_tweet(
        self,
        line_item_id: str,
        tweet_id: str,
    ) -> dict | None:
        """Promote an existing tweet under a line item.

        Returns {"id": "promoted_tweet_id"} or None.
        """
        if not self.configured:
            return None

        result = await self._post(
            f"accounts/{self._account_id}/promoted_tweets",
            {
                "line_item_id": line_item_id,
                "tweet_ids": [tweet_id],
            },
        )
        if not result:
            return None

        data = result.get("data", [result])
        if isinstance(data, list) and data:
            return {"id": data[0].get("id")}
        return {"id": data.get("id")} if isinstance(data, dict) else None

    async def create_tweet(
        self,
        text: str,
        link_url: str | None = None,
    ) -> dict | None:
        """Create a tweet via the Ads API tweet creation endpoint.

        Returns {"tweet_id": "..."} or None.
        """
        if not self.configured:
            return None

        full_text = text
        if link_url:
            full_text = f"{text[:250]} {link_url}" if len(text) > 250 else f"{text} {link_url}"
        full_text = full_text[:280]

        result = await self._post(
            f"accounts/{self._account_id}/tweet",
            {
                "text": full_text,
                "as_super_admin": True,
            },
        )
        if not result:
            return None

        data = result.get("data", result)
        tweet_id = data.get("id") or data.get("id_str") or data.get("tweet_id")
        return {"tweet_id": tweet_id} if tweet_id else None

    async def add_targeting(
        self,
        line_item_id: str,
        targeting: dict | None = None,
    ) -> list[dict]:
        """Apply targeting criteria to a line item.

        Returns list of created targeting criteria.
        """
        if not self.configured or not targeting:
            return []

        criteria = self._build_targeting_criteria(targeting)
        created = []

        for criterion in criteria:
            result = await self._post(
                f"accounts/{self._account_id}/targeting_criteria",
                {
                    "line_item_id": line_item_id,
                    **criterion,
                },
            )
            if result:
                created.append(result.get("data", result))

        return created

    async def update_campaign_status(
        self, campaign_id: str, status: str
    ) -> dict | None:
        """Update campaign status."""
        if not self.configured:
            return None
        tw_status = STATUS_MAP.get(status.lower(), status.upper())
        return await self._put(
            f"accounts/{self._account_id}/campaigns/{campaign_id}",
            {"entity_status": tw_status},
        )

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    async def get_campaign_stats(
        self,
        campaign_id: str,
    ) -> dict | None:
        """Fetch campaign stats from Twitter Analytics.

        Returns normalized performance dict or None.
        """
        if not self.configured:
            return None

        result = await self._get(
            f"stats/accounts/{self._account_id}",
            {
                "entity": "CAMPAIGN",
                "entity_ids": campaign_id,
                "metric_groups": "ENGAGEMENT,BILLING,WEB_CONVERSION",
                "granularity": "TOTAL",
                "start_time": "2026-01-01",
                "end_time": "2026-12-31",
            },
        )
        if not result:
            return None

        data = result.get("data", [])
        if not data:
            return None

        metrics = data[0].get("id_data", [{}])[0].get("metrics", {})

        impressions = int(metrics.get("impressions", [0])[0]) if metrics.get("impressions") else 0
        clicks = int(metrics.get("clicks", [0])[0]) if metrics.get("clicks") else 0
        spend_micros = int(metrics.get("billed_charge_local_micro", [0])[0]) if metrics.get("billed_charge_local_micro") else 0
        conversions = int(metrics.get("conversion_purchases_order_quantity", [0])[0]) if metrics.get("conversion_purchases_order_quantity") else 0

        spend_cents = spend_micros // 10_000
        cpc_cents = spend_cents // max(clicks, 1) if clicks else 0
        ctr = (clicks / max(impressions, 1)) * 100 if impressions else 0.0

        return {
            "impressions": impressions,
            "clicks": clicks,
            "spend_cents": spend_cents,
            "cpc_cents": cpc_cents,
            "ctr": round(ctr, 2),
            "conversions": conversions,
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
        """End-to-end: create campaign → line item → tweets → promote → target.

        Returns dict with all created IDs, or None on failure.
        """
        if not self.configured:
            log.info("Twitter Ads not configured — skipping push")
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

        # Step 2: Create line item
        line_item = await self.create_line_item(
            campaign_id=campaign_id,
            name=f"{campaign_name} — Line Item",
            campaign_type=campaign_type,
        )
        if not line_item or not line_item.get("id"):
            return None
        line_item_id = line_item["id"]

        # Step 3: Create tweets and promote them
        promoted = []
        for i, copy in enumerate(ad_copies):
            headline = copy.get("headline", "")
            body_text = copy.get("body", "")
            tweet_text = f"{headline}\n\n{body_text}".strip()

            tweet = await self.create_tweet(tweet_text, link_url)
            if not tweet or not tweet.get("tweet_id"):
                continue

            promo = await self.create_promoted_tweet(line_item_id, tweet["tweet_id"])
            if promo and promo.get("id"):
                promoted.append({
                    "promoted_tweet_id": promo["id"],
                    "tweet_id": tweet["tweet_id"],
                    "headline": headline,
                })

        if not promoted:
            log.warning("No promoted tweets created for Twitter campaign %s", campaign_id)
            return None

        # Step 4: Apply targeting
        if targeting:
            await self.add_targeting(line_item_id, targeting)

        # Step 5: Activate
        await self.update_campaign_status(campaign_id, "active")

        result = {
            "twitter_campaign_id": campaign_id,
            "twitter_line_item_id": line_item_id,
            "promoted_tweets": promoted,
            "total_promoted": len(promoted),
            "status": "ACTIVE",
        }
        log.info(
            "Pushed campaign to Twitter: %s (%d promoted tweets)",
            campaign_id, len(promoted),
        )
        return result

    async def sync_campaign_performance(self, campaign_id: str) -> dict | None:
        """Fetch and normalize Twitter campaign metrics."""
        return await self.get_campaign_stats(campaign_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_targeting_criteria(targeting: dict) -> list[dict]:
        """Convert Arclane segments to Twitter targeting criteria.

        Twitter supports: locations, interests, keywords, follower_lookalikes,
        platforms, languages, gender.
        """
        criteria = []

        demographics = targeting.get("demographics") or {}
        psychographics = targeting.get("psychographics") or {}

        # Location
        location = demographics.get("location")
        if location:
            if isinstance(location, str):
                criteria.append({
                    "targeting_type": "LOCATION",
                    "targeting_value": location,
                })
            elif isinstance(location, list):
                for loc in location[:5]:
                    criteria.append({
                        "targeting_type": "LOCATION",
                        "targeting_value": loc,
                    })

        # Interests
        interests = psychographics.get("interests", [])
        for interest in interests[:10]:
            criteria.append({
                "targeting_type": "INTEREST",
                "targeting_value": interest,
            })

        # From segments
        for seg in targeting.get("segments", []):
            seg_psycho = seg.get("psychographics") or {}
            for interest in seg_psycho.get("interests", [])[:5]:
                criteria.append({
                    "targeting_type": "INTEREST",
                    "targeting_value": interest,
                })

        # Gender
        gender = demographics.get("gender", "")
        if gender.lower() in ("male", "female"):
            criteria.append({
                "targeting_type": "GENDER",
                "targeting_value": "1" if gender.lower() == "male" else "2",
            })

        return criteria
