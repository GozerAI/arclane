"""Tests for Google Ads integration — client, service wiring, and performance sync."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.integrations.google_ads_client import (
    GoogleAdsClient,
    CAMPAIGN_TYPE_MAP,
    STATUS_MAP,
)
from arclane.integrations.meta_ads_client import MetaAdsClient
from arclane.models.tables import AdCampaign, AdCopy, Business, CustomerSegment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def business(db_session: AsyncSession) -> Business:
    biz = Business(
        slug="google-test",
        name="Google Test Corp",
        description="An e-commerce store selling premium office supplies.",
        website_url="https://googletest.com",
        owner_email="google@test.com",
        plan="pro",
        working_days_remaining=10,
    )
    db_session.add(biz)
    await db_session.flush()
    return biz


@pytest.fixture
def google_client():
    """Google Ads client with test credentials."""
    return GoogleAdsClient(
        developer_token="dev-token-123",
        customer_id="123-456-7890",
        refresh_token="refresh-token-123",
        client_id="client-id-123",
        client_secret="client-secret-123",
        base_url="https://googleads.googleapis.com/v17",
    )


@pytest.fixture
def unconfigured_client():
    """Google Ads client with no credentials."""
    return GoogleAdsClient(
        developer_token="", customer_id="", refresh_token="",
        client_id="", client_secret="",
    )


@pytest.fixture
def mock_llm():
    client = AsyncMock()
    client.generate = AsyncMock(return_value=None)
    client.model_for_area = lambda area: "test-model"
    client.enabled = True
    return client


def _mock_httpx_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _mock_async_client(method_name, return_value):
    mock_client = AsyncMock()
    getattr(mock_client, method_name).return_value = return_value
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.fixture(autouse=True)
def disable_resilience():
    """Force httpx path in tests."""
    with patch("arclane.integrations.google_ads_client._HAS_RESILIENCE", False):
        yield


# ---------------------------------------------------------------------------
# Client: Configuration
# ---------------------------------------------------------------------------


class TestGoogleClientConfig:
    def test_configured_with_credentials(self, google_client):
        assert google_client.configured is True

    def test_not_configured_without_credentials(self, unconfigured_client):
        assert unconfigured_client.configured is False

    def test_customer_id_strips_dashes(self, google_client):
        assert google_client._customer_id == "1234567890"

    def test_default_base_url(self):
        client = GoogleAdsClient(
            developer_token="t", customer_id="1", refresh_token="r",
            client_id="c", client_secret="s",
        )
        assert "googleads.googleapis.com" in client._base_url


# ---------------------------------------------------------------------------
# Client: Auth
# ---------------------------------------------------------------------------


class TestGoogleAuth:
    async def test_ensure_access_token_success(self, google_client):
        resp = _mock_httpx_response({"access_token": "ya29.test_token"})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            token = await google_client._ensure_access_token()
            assert token == "ya29.test_token"

    async def test_ensure_access_token_cached(self, google_client):
        google_client._access_token = "cached_token"
        token = await google_client._ensure_access_token()
        assert token == "cached_token"

    async def test_ensure_access_token_failure(self, google_client):
        mock = AsyncMock()
        mock.post.side_effect = Exception("auth failed")
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            token = await google_client._ensure_access_token()
            assert token is None


# ---------------------------------------------------------------------------
# Client: Campaign Creation
# ---------------------------------------------------------------------------


class TestGoogleCampaignCreation:
    async def test_create_campaign_success(self, google_client):
        google_client._access_token = "test_token"

        budget_resp = _mock_httpx_response(
            {"results": [{"resourceName": "customers/1234567890/campaignBudgets/111"}]}
        )
        campaign_resp = _mock_httpx_response(
            {"results": [{"resourceName": "customers/1234567890/campaigns/222"}]}
        )

        mock = AsyncMock()
        mock.post = AsyncMock(side_effect=[budget_resp, campaign_resp])
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.create_campaign("Test Campaign", "traffic", 1000)
            assert result is not None
            assert "campaigns/222" in result["campaign_resource_name"]
            assert "campaignBudgets/111" in result["budget_resource_name"]

    async def test_create_campaign_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.create_campaign("Test")
        assert result is None

    async def test_create_campaign_budget_failure(self, google_client):
        google_client._access_token = "test_token"
        resp = _mock_httpx_response({"error": "bad"})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.create_campaign("Fail")
            assert result is None

    async def test_create_campaign_types(self, google_client):
        """Each campaign type maps to correct channel and bidding."""
        for arclane_type, config in CAMPAIGN_TYPE_MAP.items():
            assert "advertising_channel_type" in config
            assert "bidding_strategy_type" in config


# ---------------------------------------------------------------------------
# Client: Ad Group Creation
# ---------------------------------------------------------------------------


class TestGoogleAdGroupCreation:
    async def test_create_ad_group_success(self, google_client):
        google_client._access_token = "test_token"
        resp = _mock_httpx_response(
            {"results": [{"resourceName": "customers/1234567890/adGroups/333"}]}
        )
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.create_ad_group(
                "customers/1234567890/campaigns/222", "Test Ad Group",
            )
            assert result is not None
            assert "adGroups/333" in result["ad_group_resource_name"]

    async def test_create_ad_group_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.create_ad_group("camp/1", "Test")
        assert result is None


# ---------------------------------------------------------------------------
# Client: Ad Creation
# ---------------------------------------------------------------------------


class TestGoogleAdCreation:
    async def test_create_responsive_search_ad(self, google_client):
        google_client._access_token = "test_token"
        resp = _mock_httpx_response(
            {"results": [{"resourceName": "customers/1234567890/adGroupAds/444"}]}
        )
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.create_responsive_search_ad(
                "customers/1234567890/adGroups/333",
                headlines=["Headline 1", "Headline 2", "Headline 3"],
                descriptions=["Desc 1", "Desc 2"],
                final_url="https://example.com",
            )
            assert result is not None
            assert "adGroupAds/444" in result["ad_resource_name"]

    async def test_create_ad_pads_headlines(self, google_client):
        """Headlines are padded to minimum 3 if fewer provided."""
        google_client._access_token = "test_token"
        resp = _mock_httpx_response({"results": [{"resourceName": "rn/1"}]})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.create_responsive_search_ad(
                "adgroup/1", headlines=["One"], descriptions=["D1", "D2"],
                final_url="https://example.com",
            )
            assert result is not None
            # Verify payload had at least 3 headlines
            call_body = mock.post.call_args[1]["json"]
            ad = call_body["operations"][0]["create"]["ad"]["responsiveSearchAd"]
            assert len(ad["headlines"]) >= 3

    async def test_create_ad_truncates_headlines(self, google_client):
        """Headlines longer than 30 chars are truncated."""
        google_client._access_token = "test_token"
        resp = _mock_httpx_response({"results": [{"resourceName": "rn/1"}]})
        mock = _mock_async_client("post", resp)

        long_headline = "X" * 50
        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            await google_client.create_responsive_search_ad(
                "adgroup/1", headlines=[long_headline, "H2", "H3"],
                descriptions=["D1", "D2"], final_url="https://example.com",
            )
            call_body = mock.post.call_args[1]["json"]
            headlines = call_body["operations"][0]["create"]["ad"]["responsiveSearchAd"]["headlines"]
            assert len(headlines[0]["text"]) == 30

    async def test_create_ad_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.create_responsive_search_ad(
            "adgroup/1", ["H1", "H2", "H3"], ["D1", "D2"], "url",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Client: Keywords
# ---------------------------------------------------------------------------


class TestGoogleKeywords:
    async def test_add_keywords_success(self, google_client):
        google_client._access_token = "test_token"
        resp = _mock_httpx_response({
            "results": [
                {"resourceName": "kw/1"},
                {"resourceName": "kw/2"},
            ]
        })
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            results = await google_client.add_keywords(
                "adgroup/1", ["office supplies", "desk organizer"],
            )
            assert len(results) == 2

    async def test_add_keywords_empty(self, google_client):
        results = await google_client.add_keywords("adgroup/1", [])
        assert results == []

    async def test_add_keywords_unconfigured(self, unconfigured_client):
        results = await unconfigured_client.add_keywords("ag/1", ["test"])
        assert results == []


# ---------------------------------------------------------------------------
# Client: Full Campaign Push
# ---------------------------------------------------------------------------


class TestGooglePushCampaign:
    async def test_push_campaign_full_flow(self, google_client):
        """Full push: budget → campaign → ad group → ad → keywords → activate."""
        google_client._access_token = "test_token"

        responses = [
            # create campaign: budget
            _mock_httpx_response({"results": [{"resourceName": "budget/1"}]}),
            # create campaign: campaign
            _mock_httpx_response({"results": [{"resourceName": "campaign/1"}]}),
            # create ad group
            _mock_httpx_response({"results": [{"resourceName": "adgroup/1"}]}),
            # create responsive search ad
            _mock_httpx_response({"results": [{"resourceName": "ad/1"}]}),
            # add keywords
            _mock_httpx_response({"results": [{"resourceName": "kw/1"}]}),
            # update status to active
            _mock_httpx_response({"results": [{"resourceName": "campaign/1"}]}),
        ]

        mock = AsyncMock()
        mock.post = AsyncMock(side_effect=responses)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.push_campaign(
                campaign_name="Full Test",
                campaign_type="traffic",
                daily_budget_cents=1000,
                ad_copies=[
                    {"headline": "Premium Office Supplies", "body": "Free shipping on orders over $50.", "cta": "Shop Now"},
                    {"headline": "Desk Organizers", "body": "Transform your workspace.", "cta": "Browse"},
                ],
                targeting={
                    "psychographics": {"interests": ["office", "productivity"]},
                },
                link_url="https://example.com",
            )
            assert result is not None
            assert result["google_campaign_resource_name"] == "campaign/1"
            assert result["google_ad_group_resource_name"] == "adgroup/1"
            assert result["status"] == "ENABLED"
            assert result["keywords_added"] >= 1

    async def test_push_campaign_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.push_campaign(
            campaign_name="Nope", campaign_type="traffic",
            daily_budget_cents=500, ad_copies=[{"headline": "H", "body": "B"}],
            link_url="https://example.com",
        )
        assert result is None

    async def test_push_campaign_creation_fails(self, google_client):
        with patch.object(google_client, "create_campaign", AsyncMock(return_value=None)):
            result = await google_client.push_campaign(
                campaign_name="Fail", campaign_type="traffic",
                daily_budget_cents=500, ad_copies=[{"headline": "H", "body": "B"}],
                link_url="url",
            )
            assert result is None

    async def test_push_campaign_ad_group_fails(self, google_client):
        with patch.object(google_client, "create_campaign", AsyncMock(return_value={
            "campaign_resource_name": "camp/1", "budget_resource_name": "budget/1",
        })):
            with patch.object(google_client, "create_ad_group", AsyncMock(return_value=None)):
                result = await google_client.push_campaign(
                    campaign_name="Fail", campaign_type="traffic",
                    daily_budget_cents=500, ad_copies=[{"headline": "H", "body": "B"}],
                    link_url="url",
                )
                assert result is None


# ---------------------------------------------------------------------------
# Client: Performance
# ---------------------------------------------------------------------------


class TestGooglePerformance:
    async def test_get_campaign_performance(self, google_client):
        google_client._access_token = "test_token"
        search_resp = _mock_httpx_response([{
            "results": [
                {
                    "metrics": {
                        "impressions": "8000",
                        "clicks": "400",
                        "costMicros": "500000000",  # $50.00
                        "conversions": "20",
                        "averageCpc": "1250000",  # $1.25
                        "ctr": "0.05",
                    }
                },
            ]
        }])
        mock = _mock_async_client("post", search_resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.get_campaign_performance("customers/123/campaigns/456")
            assert result is not None
            assert result["impressions"] == 8000
            assert result["clicks"] == 400
            assert result["spend_cents"] == 50000  # $50.00 in cents
            assert result["conversions"] == 20

    async def test_get_performance_no_data(self, google_client):
        google_client._access_token = "test_token"
        resp = _mock_httpx_response([{"results": []}])
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.get_campaign_performance("camp/1")
            assert result is None

    async def test_get_performance_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.get_campaign_performance("camp/1")
        assert result is None

    async def test_sync_delegates_to_get(self, google_client):
        """sync_campaign_performance is an alias for get_campaign_performance."""
        with patch.object(google_client, "get_campaign_performance", AsyncMock(return_value={"clicks": 5})) as mock:
            result = await google_client.sync_campaign_performance("camp/1")
            assert result == {"clicks": 5}
            mock.assert_called_once_with("camp/1")


# ---------------------------------------------------------------------------
# Client: Status Updates
# ---------------------------------------------------------------------------


class TestGoogleStatusUpdates:
    async def test_update_campaign_status(self, google_client):
        google_client._access_token = "test_token"
        resp = _mock_httpx_response({"results": [{}]})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.google_ads_client.httpx.AsyncClient", return_value=mock):
            result = await google_client.update_campaign_status("camp/1", "paused")
            assert result is not None

    async def test_update_status_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.update_campaign_status("camp/1", "active")
        assert result is None


# ---------------------------------------------------------------------------
# Client: Keyword Extraction
# ---------------------------------------------------------------------------


class TestKeywordExtraction:
    def test_from_interests(self):
        keywords = GoogleAdsClient._extract_keywords(
            {"psychographics": {"interests": ["AI", "automation"]}},
            [],
        )
        assert "AI" in keywords
        assert "automation" in keywords

    def test_from_segments(self):
        keywords = GoogleAdsClient._extract_keywords(
            {"segments": [{"psychographics": {"interests": ["SaaS"], "pain_points": ["pricing"]}}]},
            [],
        )
        assert "SaaS" in keywords
        assert "pricing" in keywords

    def test_from_ad_headlines(self):
        keywords = GoogleAdsClient._extract_keywords(
            {}, [{"headline": "Premium Office Supplies"}],
        )
        assert "Premium Office Supplies" in keywords

    def test_deduplication(self):
        keywords = GoogleAdsClient._extract_keywords(
            {"psychographics": {"interests": ["AI", "ai", "AI"]}},
            [{"headline": "ai"}],
        )
        assert len(keywords) == 1

    def test_max_20(self):
        keywords = GoogleAdsClient._extract_keywords(
            {"psychographics": {"interests": [f"kw{i}" for i in range(30)]}},
            [],
        )
        assert len(keywords) <= 20

    def test_empty_targeting(self):
        keywords = GoogleAdsClient._extract_keywords({}, [])
        assert keywords == []


# ---------------------------------------------------------------------------
# Service: launch_campaign with Google Push
# ---------------------------------------------------------------------------


class TestLaunchWithGoogle:
    async def test_launch_pushes_to_google(self, db_session, business):
        """When Google is configured, launch_campaign pushes to the API."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="Google Push", platform="google",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="Office Supplies",
            body="Free shipping.", platform_format="text", tone="professional",
        ))
        await db_session.flush()

        mock_google = AsyncMock(spec=GoogleAdsClient)
        mock_google.configured = True
        mock_google.push_campaign = AsyncMock(return_value={
            "google_campaign_resource_name": "customers/123/campaigns/456",
            "google_budget_resource_name": "customers/123/campaignBudgets/789",
            "google_ad_group_resource_name": "customers/123/adGroups/111",
            "google_ad_resource_name": "customers/123/adGroupAds/222",
            "keywords_added": 3,
            "total_headlines": 2,
            "total_descriptions": 1,
            "status": "ENABLED",
        })

        result = await launch_campaign(
            business, campaign.id, db_session, google_client=mock_google,
        )
        assert result["status"] == "active"
        assert "google" in result
        assert result["google"]["google_campaign_resource_name"] == "customers/123/campaigns/456"
        mock_google.push_campaign.assert_called_once()

    async def test_launch_stores_google_ids(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="Store IDs", platform="google",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="text", tone="professional",
        ))
        await db_session.flush()

        mock_google = AsyncMock(spec=GoogleAdsClient)
        mock_google.configured = True
        mock_google.push_campaign = AsyncMock(return_value={
            "google_campaign_resource_name": "camp/1",
            "google_budget_resource_name": "budget/1",
            "google_ad_group_resource_name": "adgroup/1",
            "google_ad_resource_name": "ad/1",
            "keywords_added": 0,
            "total_headlines": 1,
            "total_descriptions": 1,
            "status": "ENABLED",
        })

        await launch_campaign(business, campaign.id, db_session, google_client=mock_google)
        refreshed = await db_session.get(AdCampaign, campaign.id)
        assert refreshed.metadata_json["google"]["google_campaign_resource_name"] == "camp/1"

    async def test_launch_google_failure_still_activates(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="Fail Push", platform="google",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="text", tone="professional",
        ))
        await db_session.flush()

        mock_google = AsyncMock(spec=GoogleAdsClient)
        mock_google.configured = True
        mock_google.push_campaign = AsyncMock(return_value=None)

        result = await launch_campaign(
            business, campaign.id, db_session, google_client=mock_google,
        )
        assert result["status"] == "active"
        assert "google" not in result

    async def test_launch_no_google_for_facebook(self, db_session, business):
        """Facebook campaigns don't push to Google."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="FB Only", platform="facebook",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="professional",
        ))
        await db_session.flush()

        mock_google = AsyncMock(spec=GoogleAdsClient)
        mock_google.configured = True
        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = False

        result = await launch_campaign(
            business, campaign.id, db_session,
            meta_client=mock_meta, google_client=mock_google,
        )
        assert result["status"] == "active"
        mock_google.push_campaign.assert_not_called()

    async def test_launch_unconfigured_google_skips(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="No Google", platform="google",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="text", tone="professional",
        ))
        await db_session.flush()

        mock_google = AsyncMock(spec=GoogleAdsClient)
        mock_google.configured = False

        result = await launch_campaign(
            business, campaign.id, db_session, google_client=mock_google,
        )
        assert result["status"] == "active"
        mock_google.push_campaign.assert_not_called()


# ---------------------------------------------------------------------------
# Service: sync_campaign_performance with Google
# ---------------------------------------------------------------------------


class TestSyncPerformanceGoogle:
    async def test_sync_google_success(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(
            business, db_session, name="Sync Google", platform="google",
        )
        campaign.metadata_json = {
            "google": {"google_campaign_resource_name": "customers/123/campaigns/456"}
        }
        await db_session.flush()

        mock_google = AsyncMock(spec=GoogleAdsClient)
        mock_google.configured = True
        mock_google.sync_campaign_performance = AsyncMock(return_value={
            "impressions": 5000,
            "clicks": 200,
            "spend_cents": 3000,
            "cpc_cents": 15,
            "ctr": 4.0,
            "conversions": 10,
        })

        result = await sync_campaign_performance(
            business, campaign.id, db_session, google_client=mock_google,
        )
        assert result["performance"]["impressions"] == 5000
        assert result["performance"]["conversions"] == 10

        refreshed = await db_session.get(AdCampaign, campaign.id)
        assert refreshed.spent_cents == 3000

    async def test_sync_google_failure(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(
            business, db_session, name="Fail Sync", platform="google",
        )
        campaign.metadata_json = {
            "google": {"google_campaign_resource_name": "camp/1"}
        }
        await db_session.flush()

        mock_google = AsyncMock(spec=GoogleAdsClient)
        mock_google.configured = True
        mock_google.sync_campaign_performance = AsyncMock(return_value=None)

        result = await sync_campaign_performance(
            business, campaign.id, db_session, google_client=mock_google,
        )
        assert "error" in result

    async def test_sync_no_platform_integration(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(
            business, db_session, name="No Platform", platform="google",
        )
        result = await sync_campaign_performance(business, campaign.id, db_session)
        assert "error" in result
        assert "nothing to sync" in result["error"]


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------


class TestMaps:
    def test_all_campaign_types_mapped(self):
        for t in ("awareness", "traffic", "conversion", "retargeting"):
            assert t in CAMPAIGN_TYPE_MAP
            config = CAMPAIGN_TYPE_MAP[t]
            assert "advertising_channel_type" in config
            assert "bidding_strategy_type" in config

    def test_all_statuses_mapped(self):
        for s in ("active", "paused", "draft"):
            assert s in STATUS_MAP
