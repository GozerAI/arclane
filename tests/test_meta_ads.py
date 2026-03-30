"""Tests for Meta Ads integration — client, service wiring, and performance sync."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.integrations.meta_ads_client import (
    MetaAdsClient,
    OBJECTIVE_MAP,
    STATUS_MAP,
    CTA_MAP,
    _resolve_cta,
)
from arclane.models.tables import AdCampaign, AdCopy, Business, CustomerSegment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def business(db_session: AsyncSession) -> Business:
    biz = Business(
        slug="meta-test",
        name="Meta Test Corp",
        description="A productivity SaaS for remote teams.",
        website_url="https://metatest.com",
        owner_email="meta@test.com",
        plan="pro",
        working_days_remaining=10,
    )
    db_session.add(biz)
    await db_session.flush()
    return biz


@pytest.fixture
def meta_client():
    """Meta client with test credentials."""
    return MetaAdsClient(
        access_token="test-token-123",
        ad_account_id="act_123456",
        base_url="https://graph.facebook.com/v21.0",
    )


@pytest.fixture
def unconfigured_client():
    """Meta client with no credentials."""
    return MetaAdsClient(access_token="", ad_account_id="")


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
    """Force httpx path in tests by disabling gozerai_telemetry resilience."""
    with patch("arclane.integrations.meta_ads_client._HAS_RESILIENCE", False):
        yield


# ---------------------------------------------------------------------------
# Client: Configuration
# ---------------------------------------------------------------------------


class TestMetaClientConfig:
    def test_configured_with_credentials(self, meta_client):
        assert meta_client.configured is True

    def test_not_configured_without_credentials(self, unconfigured_client):
        assert unconfigured_client.configured is False

    def test_default_base_url(self):
        client = MetaAdsClient(access_token="t", ad_account_id="a")
        assert "graph.facebook.com" in client._base_url


# ---------------------------------------------------------------------------
# Client: Campaign Creation
# ---------------------------------------------------------------------------


class TestMetaCampaignCreation:
    async def test_create_campaign_success(self, meta_client):
        resp = _mock_httpx_response({"id": "camp_001"})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.create_campaign("Test Campaign", "awareness")
            assert result == {"id": "camp_001"}

    async def test_create_campaign_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.create_campaign("Test")
        assert result is None

    async def test_create_campaign_network_error(self, meta_client):
        mock = AsyncMock()
        mock.post.side_effect = Exception("Connection refused")
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.create_campaign("Test")
            assert result is None

    async def test_create_campaign_objectives(self, meta_client):
        """Each campaign type maps to a Meta objective."""
        for arclane_type, meta_obj in OBJECTIVE_MAP.items():
            resp = _mock_httpx_response({"id": f"camp_{arclane_type}"})
            mock = _mock_async_client("post", resp)
            with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
                result = await meta_client.create_campaign(f"Test {arclane_type}", arclane_type)
                assert result is not None
                # Verify objective was sent in the payload
                call_data = mock.post.call_args
                assert meta_obj in str(call_data)


# ---------------------------------------------------------------------------
# Client: Ad Set Creation
# ---------------------------------------------------------------------------


class TestMetaAdSetCreation:
    async def test_create_ad_set_success(self, meta_client):
        resp = _mock_httpx_response({"id": "adset_001"})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.create_ad_set(
                campaign_id="camp_001",
                name="Test Ad Set",
                daily_budget_cents=1000,
                targeting={"demographics": {"age_range": "25-45"}},
            )
            assert result == {"id": "adset_001"}

    async def test_create_ad_set_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.create_ad_set("camp_001", "Test")
        assert result is None

    async def test_create_ad_set_with_platforms(self, meta_client):
        resp = _mock_httpx_response({"id": "adset_002"})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.create_ad_set(
                campaign_id="camp_001",
                name="Instagram Only",
                platforms=["instagram"],
            )
            assert result is not None


# ---------------------------------------------------------------------------
# Client: Creative & Ad Creation
# ---------------------------------------------------------------------------


class TestMetaCreativeCreation:
    async def test_create_ad_creative_success(self, meta_client):
        resp = _mock_httpx_response({"id": "creative_001"})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.create_ad_creative(
                name="Test Creative",
                headline="Boost Productivity",
                body="Try our app today.",
                cta="Sign Up",
                link_url="https://example.com",
            )
            assert result == {"id": "creative_001"}

    async def test_create_ad_creative_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.create_ad_creative("Test", "H", "B")
        assert result is None

    async def test_create_ad_success(self, meta_client):
        resp = _mock_httpx_response({"id": "ad_001"})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.create_ad(
                ad_set_id="adset_001",
                creative_id="creative_001",
                name="Test Ad",
            )
            assert result == {"id": "ad_001"}


# ---------------------------------------------------------------------------
# Client: Full Campaign Push
# ---------------------------------------------------------------------------


class TestMetaPushCampaign:
    async def test_push_campaign_full_flow(self, meta_client):
        """Full push: campaign → ad set → creatives → ads → activate."""
        responses = [
            _mock_httpx_response({"id": "camp_001"}),   # create campaign
            _mock_httpx_response({"id": "adset_001"}),   # create ad set
            _mock_httpx_response({"id": "creative_001"}),# creative 1
            _mock_httpx_response({"id": "ad_001"}),      # ad 1
            _mock_httpx_response({"id": "creative_002"}),# creative 2
            _mock_httpx_response({"id": "ad_002"}),      # ad 2
            _mock_httpx_response({"success": True}),     # activate
        ]

        mock = AsyncMock()
        mock.post = AsyncMock(side_effect=responses)
        mock.get = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.push_campaign(
                campaign_name="Full Test",
                campaign_type="traffic",
                daily_budget_cents=1000,
                ad_copies=[
                    {"headline": "H1", "body": "B1", "cta": "Learn More"},
                    {"headline": "H2", "body": "B2", "cta": "Sign Up"},
                ],
                targeting={"demographics": {"age_range": "25-45"}},
                link_url="https://example.com",
            )
            assert result is not None
            assert result["meta_campaign_id"] == "camp_001"
            assert result["meta_ad_set_id"] == "adset_001"
            assert result["total_ads"] == 2
            assert result["status"] == "ACTIVE"

    async def test_push_campaign_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.push_campaign(
            campaign_name="Nope",
            campaign_type="awareness",
            daily_budget_cents=500,
            ad_copies=[{"headline": "H", "body": "B"}],
        )
        assert result is None

    async def test_push_campaign_campaign_creation_fails(self, meta_client):
        mock = AsyncMock()
        mock.post = AsyncMock(return_value=_mock_httpx_response({"error": "bad"}))
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        # Patch the _post method to return None (simulating failure)
        with patch.object(meta_client, "create_campaign", AsyncMock(return_value=None)):
            result = await meta_client.push_campaign(
                campaign_name="Fail", campaign_type="awareness",
                daily_budget_cents=500, ad_copies=[{"headline": "H", "body": "B"}],
            )
            assert result is None

    async def test_push_campaign_no_ads_created(self, meta_client):
        """If creative creation fails for all copies, push returns None."""
        with patch.object(meta_client, "create_campaign", AsyncMock(return_value={"id": "camp_001"})):
            with patch.object(meta_client, "create_ad_set", AsyncMock(return_value={"id": "adset_001"})):
                with patch.object(meta_client, "create_ad_creative", AsyncMock(return_value=None)):
                    result = await meta_client.push_campaign(
                        campaign_name="No Ads", campaign_type="awareness",
                        daily_budget_cents=500, ad_copies=[{"headline": "H", "body": "B"}],
                    )
                    assert result is None


# ---------------------------------------------------------------------------
# Client: Performance Sync
# ---------------------------------------------------------------------------


class TestMetaPerformanceSync:
    async def test_sync_campaign_performance_success(self, meta_client):
        insights_data = {
            "data": [{
                "impressions": "5000",
                "clicks": "250",
                "spend": "45.50",
                "cpc": "0.18",
                "ctr": "5.0",
                "actions": [
                    {"action_type": "purchase", "value": "12"},
                    {"action_type": "link_click", "value": "250"},
                ],
            }],
        }
        resp = _mock_httpx_response(insights_data)
        mock = _mock_async_client("get", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.sync_campaign_performance("camp_001")
            assert result is not None
            assert result["impressions"] == 5000
            assert result["clicks"] == 250
            assert result["spend_cents"] == 4550
            assert result["cpc_cents"] == 18
            assert result["ctr"] == 5.0
            assert result["conversions"] == 12

    async def test_sync_performance_empty_data(self, meta_client):
        resp = _mock_httpx_response({"data": []})
        mock = _mock_async_client("get", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.sync_campaign_performance("camp_001")
            assert result is None

    async def test_sync_performance_network_error(self, meta_client):
        mock = AsyncMock()
        mock.get.side_effect = Exception("timeout")
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.sync_campaign_performance("camp_001")
            assert result is None

    async def test_sync_performance_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.sync_campaign_performance("camp_001")
        assert result is None


# ---------------------------------------------------------------------------
# Client: Status Updates
# ---------------------------------------------------------------------------


class TestMetaStatusUpdates:
    async def test_update_campaign_status(self, meta_client):
        resp = _mock_httpx_response({"success": True})
        mock = _mock_async_client("post", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.update_campaign_status("camp_001", "PAUSED")
            assert result is not None

    async def test_update_status_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.update_campaign_status("camp_001", "ACTIVE")
        assert result is None


# ---------------------------------------------------------------------------
# Client: Targeting Spec Builder
# ---------------------------------------------------------------------------


class TestTargetingSpecBuilder:
    def test_empty_targeting(self):
        spec = MetaAdsClient._build_targeting_spec({})
        assert spec == {}

    def test_age_range(self):
        spec = MetaAdsClient._build_targeting_spec({"demographics": {"age_range": "25-45"}})
        assert spec["age_min"] == 25
        assert spec["age_max"] == 45

    def test_gender_male(self):
        spec = MetaAdsClient._build_targeting_spec({"demographics": {"gender": "male"}})
        assert spec["genders"] == [1]

    def test_gender_female(self):
        spec = MetaAdsClient._build_targeting_spec({"demographics": {"gender": "female"}})
        assert spec["genders"] == [2]

    def test_country_code(self):
        spec = MetaAdsClient._build_targeting_spec({"demographics": {"location": "US"}})
        assert spec["geo_locations"] == {"countries": ["US"]}

    def test_country_list(self):
        spec = MetaAdsClient._build_targeting_spec({"demographics": {"location": ["us", "gb"]}})
        assert spec["geo_locations"] == {"countries": ["US", "GB"]}

    def test_interests(self):
        spec = MetaAdsClient._build_targeting_spec({
            "psychographics": {"interests": ["technology", "startups"]},
        })
        assert "flexible_spec" in spec
        assert len(spec["flexible_spec"][0]["interests"]) == 2

    def test_behaviors(self):
        spec = MetaAdsClient._build_targeting_spec({
            "behaviors": {"buying_patterns": ["online shopping"]},
        })
        assert "flexible_spec" in spec
        assert "behaviors" in spec["flexible_spec"][0]

    def test_segments_targeting(self):
        spec = MetaAdsClient._build_targeting_spec({
            "segments": [
                {"name": "Tech", "demographics": {}, "psychographics": {"interests": ["AI", "SaaS"]}},
            ],
        })
        assert "flexible_spec" in spec

    def test_invalid_age_range(self):
        spec = MetaAdsClient._build_targeting_spec({"demographics": {"age_range": "invalid"}})
        assert "age_min" not in spec

    def test_interests_max_10(self):
        interests = [f"interest_{i}" for i in range(20)]
        spec = MetaAdsClient._build_targeting_spec({
            "psychographics": {"interests": interests},
        })
        assert len(spec["flexible_spec"][0]["interests"]) == 10


# ---------------------------------------------------------------------------
# CTA Mapping
# ---------------------------------------------------------------------------


class TestCTAMapping:
    def test_known_ctas(self):
        assert _resolve_cta("Learn More") == "LEARN_MORE"
        assert _resolve_cta("shop now") == "SHOP_NOW"
        assert _resolve_cta("SIGN UP") == "SIGN_UP"

    def test_unknown_cta_defaults(self):
        assert _resolve_cta("something random") == "LEARN_MORE"

    def test_none_cta(self):
        assert _resolve_cta(None) == "LEARN_MORE"

    def test_empty_cta(self):
        assert _resolve_cta("") == "LEARN_MORE"


# ---------------------------------------------------------------------------
# Service: launch_campaign with Meta Push
# ---------------------------------------------------------------------------


class TestLaunchWithMeta:
    async def test_launch_pushes_to_meta(self, db_session, business):
        """When Meta is configured, launch_campaign pushes to the API."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="Meta Push", platform="facebook",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="professional",
        ))
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = True
        mock_meta.push_campaign = AsyncMock(return_value={
            "meta_campaign_id": "camp_meta_001",
            "meta_ad_set_id": "adset_meta_001",
            "ads": [{"ad_id": "ad_001", "creative_id": "cr_001", "headline": "H"}],
            "total_ads": 1,
            "status": "ACTIVE",
        })

        result = await launch_campaign(business, campaign.id, db_session, meta_client=mock_meta)
        assert result["status"] == "active"
        assert "meta" in result
        assert result["meta"]["meta_campaign_id"] == "camp_meta_001"
        mock_meta.push_campaign.assert_called_once()

    async def test_launch_stores_meta_ids(self, db_session, business):
        """Meta IDs are stored in campaign.metadata_json for future syncs."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="Store IDs", platform="facebook",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="professional",
        ))
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = True
        mock_meta.push_campaign = AsyncMock(return_value={
            "meta_campaign_id": "camp_002",
            "meta_ad_set_id": "adset_002",
            "ads": [{"ad_id": "ad_002"}],
            "total_ads": 1,
            "status": "ACTIVE",
        })

        await launch_campaign(business, campaign.id, db_session, meta_client=mock_meta)
        refreshed = await db_session.get(AdCampaign, campaign.id)
        assert refreshed.metadata_json["meta"]["meta_campaign_id"] == "camp_002"

    async def test_launch_meta_failure_still_activates(self, db_session, business):
        """If Meta push fails, campaign is still marked active locally."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="Fail Push", platform="facebook",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="professional",
        ))
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = True
        mock_meta.push_campaign = AsyncMock(return_value=None)

        result = await launch_campaign(business, campaign.id, db_session, meta_client=mock_meta)
        assert result["status"] == "active"
        assert "meta" not in result

    async def test_launch_no_meta_for_google(self, db_session, business):
        """Google campaigns don't push to Meta."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="Google Only", platform="google",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="text", tone="professional",
        ))
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = True

        result = await launch_campaign(business, campaign.id, db_session, meta_client=mock_meta)
        assert result["status"] == "active"
        mock_meta.push_campaign.assert_not_called()

    async def test_launch_instagram_uses_instagram_platform(self, db_session, business):
        """Instagram campaigns push to Meta with instagram-only placement."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="IG Only", platform="instagram",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="casual",
        ))
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = True
        mock_meta.push_campaign = AsyncMock(return_value={
            "meta_campaign_id": "camp_ig",
            "meta_ad_set_id": "adset_ig",
            "ads": [],
            "total_ads": 0,
            "status": "ACTIVE",
        })

        await launch_campaign(business, campaign.id, db_session, meta_client=mock_meta)
        call_kwargs = mock_meta.push_campaign.call_args.kwargs
        assert call_kwargs["platforms"] == ["instagram"]

    async def test_launch_unconfigured_meta_skips(self, db_session, business):
        """If Meta not configured, launch works normally without push."""
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(
            business, db_session, name="No Meta", platform="facebook",
        )
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="professional",
        ))
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = False

        result = await launch_campaign(business, campaign.id, db_session, meta_client=mock_meta)
        assert result["status"] == "active"
        mock_meta.push_campaign.assert_not_called()


# ---------------------------------------------------------------------------
# Service: sync_campaign_performance
# ---------------------------------------------------------------------------


class TestSyncPerformance:
    async def test_sync_success(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(
            business, db_session, name="Sync Test", platform="facebook",
        )
        campaign.metadata_json = {"meta": {"meta_campaign_id": "camp_sync_001"}}
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = True
        mock_meta.sync_campaign_performance = AsyncMock(return_value={
            "impressions": 10000,
            "clicks": 500,
            "spend_cents": 7500,
            "cpc_cents": 15,
            "ctr": 5.0,
            "conversions": 25,
        })

        result = await sync_campaign_performance(
            business, campaign.id, db_session, meta_client=mock_meta,
        )
        assert result["performance"]["impressions"] == 10000
        assert result["performance"]["conversions"] == 25

        # Verify campaign was updated
        refreshed = await db_session.get(AdCampaign, campaign.id)
        assert refreshed.performance["clicks"] == 500
        assert refreshed.spent_cents == 7500

    async def test_sync_no_meta_integration(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(
            business, db_session, name="No Meta", platform="facebook",
        )
        result = await sync_campaign_performance(business, campaign.id, db_session)
        assert "error" in result

    async def test_sync_meta_failure(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(
            business, db_session, name="Fail Sync", platform="facebook",
        )
        campaign.metadata_json = {"meta": {"meta_campaign_id": "camp_fail"}}
        await db_session.flush()

        mock_meta = AsyncMock(spec=MetaAdsClient)
        mock_meta.configured = True
        mock_meta.sync_campaign_performance = AsyncMock(return_value=None)

        result = await sync_campaign_performance(
            business, campaign.id, db_session, meta_client=mock_meta,
        )
        assert result["error"] == "Failed to fetch performance data from ad platform"

    async def test_sync_not_found(self, db_session, business):
        from arclane.services.advertising_service import sync_campaign_performance
        result = await sync_campaign_performance(business, 9999, db_session)
        assert result["error"] == "Campaign not found"


# ---------------------------------------------------------------------------
# Route: Sync Endpoint
# ---------------------------------------------------------------------------


class TestSyncRoute:
    @pytest.fixture
    async def api_client(self, db_session, business):
        from arclane.api.app import app
        from arclane.core.database import get_session
        from arclane.api.deps import get_business
        from httpx import ASGITransport, AsyncClient

        async def _override_session():
            yield db_session

        async def _override_business():
            return business

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[get_business] = _override_business

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

        app.dependency_overrides.clear()

    async def test_sync_route_no_meta(self, api_client):
        """Sync endpoint returns 400 when campaign has no Meta integration."""
        create_resp = await api_client.post(
            "/api/businesses/meta-test/advertising/campaigns",
            json={"name": "No Meta Route", "platform": "facebook"},
        )
        cid = create_resp.json()["id"]
        resp = await api_client.post(f"/api/businesses/meta-test/advertising/campaigns/{cid}/sync")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Objective & Status Maps
# ---------------------------------------------------------------------------


class TestMaps:
    def test_all_campaign_types_mapped(self):
        for t in ("awareness", "traffic", "conversion", "retargeting"):
            assert t in OBJECTIVE_MAP

    def test_all_statuses_mapped(self):
        for s in ("active", "paused", "draft"):
            assert s in STATUS_MAP

    def test_common_ctas_mapped(self):
        for cta in ("learn more", "shop now", "sign up", "buy now", "download"):
            assert cta in CTA_MAP


# ---------------------------------------------------------------------------
# Insights Fetch
# ---------------------------------------------------------------------------


class TestInsightsFetch:
    async def test_get_campaign_insights(self, meta_client):
        insights = {"data": [{"impressions": "1000"}]}
        resp = _mock_httpx_response(insights)
        mock = _mock_async_client("get", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.get_campaign_insights("camp_001")
            assert result["data"][0]["impressions"] == "1000"

    async def test_get_ad_insights(self, meta_client):
        insights = {"data": [{"clicks": "50"}]}
        resp = _mock_httpx_response(insights)
        mock = _mock_async_client("get", resp)

        with patch("arclane.integrations.meta_ads_client.httpx.AsyncClient", return_value=mock):
            result = await meta_client.get_ad_insights("ad_001")
            assert result["data"][0]["clicks"] == "50"

    async def test_get_insights_unconfigured(self, unconfigured_client):
        result = await unconfigured_client.get_campaign_insights("camp_001")
        assert result is None
