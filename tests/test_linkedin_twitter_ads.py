"""Tests for LinkedIn and Twitter/X Ads integrations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.integrations.linkedin_ads_client import LinkedInAdsClient, OBJECTIVE_MAP as LI_OBJ_MAP
from arclane.integrations.twitter_ads_client import TwitterAdsClient, OBJECTIVE_MAP as TW_OBJ_MAP
from arclane.models.tables import AdCampaign, AdCopy, Business, CustomerSegment


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def business(db_session: AsyncSession) -> Business:
    biz = Business(
        slug="ad-platform-test", name="Platform Test Inc",
        description="A B2B analytics platform for enterprise teams.",
        website_url="https://platformtest.com", owner_email="test@platform.com",
        plan="pro", working_days_remaining=10,
    )
    db_session.add(biz)
    await db_session.flush()
    return biz


def _mock_resp(json_data, status_code=200, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    resp.headers = headers or {}
    resp.content = b'{"id":"test"}'
    return resp


def _mock_client(method, return_value):
    m = AsyncMock()
    getattr(m, method).return_value = return_value
    m.__aenter__ = AsyncMock(return_value=m)
    m.__aexit__ = AsyncMock(return_value=False)
    return m


# ===========================================================================
# LINKEDIN ADS
# ===========================================================================


@pytest.fixture
def li_client():
    return LinkedInAdsClient(access_token="li-token", account_id="12345")


@pytest.fixture
def li_unconfigured():
    return LinkedInAdsClient(access_token="", account_id="")


@pytest.fixture(autouse=True)
def disable_resilience():
    with patch("arclane.integrations.linkedin_ads_client._HAS_RESILIENCE", False), \
         patch("arclane.integrations.twitter_ads_client._HAS_RESILIENCE", False):
        yield


class TestLinkedInConfig:
    def test_configured(self, li_client):
        assert li_client.configured is True

    def test_unconfigured(self, li_unconfigured):
        assert li_unconfigured.configured is False


class TestLinkedInCampaign:
    async def test_create_campaign(self, li_client):
        resp = _mock_resp({}, headers={"X-RestLi-Id": "camp_li_001"})
        mock = _mock_client("post", resp)
        with patch("arclane.integrations.linkedin_ads_client.httpx.AsyncClient", return_value=mock):
            result = await li_client.create_campaign("Test LI", "traffic", 1000)
            assert result is not None
            assert result["id"] == "camp_li_001"

    async def test_create_campaign_unconfigured(self, li_unconfigured):
        assert await li_unconfigured.create_campaign("Test") is None

    async def test_create_creative(self, li_client):
        resp = _mock_resp({}, headers={"X-RestLi-Id": "cr_li_001"})
        mock = _mock_client("post", resp)
        with patch("arclane.integrations.linkedin_ads_client.httpx.AsyncClient", return_value=mock):
            result = await li_client.create_creative("camp_1", "Headline", "Body", "Learn More", "https://example.com")
            assert result is not None
            assert result["id"] == "cr_li_001"

    async def test_create_creative_unconfigured(self, li_unconfigured):
        assert await li_unconfigured.create_creative("c", "h", "b") is None

    async def test_update_status(self, li_client):
        resp = _mock_resp({})
        mock = _mock_client("patch", resp)
        with patch("arclane.integrations.linkedin_ads_client.httpx.AsyncClient", return_value=mock):
            result = await li_client.update_campaign_status("camp_1", "active")
            assert result is not None


class TestLinkedInPush:
    async def test_push_full_flow(self, li_client):
        campaign_resp = _mock_resp({}, headers={"X-RestLi-Id": "camp_001"})
        creative_resp = _mock_resp({}, headers={"X-RestLi-Id": "cr_001"})
        patch_resp = _mock_resp({})

        mock = AsyncMock()
        mock.post = AsyncMock(side_effect=[campaign_resp, creative_resp, creative_resp])
        mock.patch = AsyncMock(side_effect=[patch_resp, patch_resp])  # targeting + activate
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.linkedin_ads_client.httpx.AsyncClient", return_value=mock):
            result = await li_client.push_campaign(
                campaign_name="LI Full", campaign_type="traffic",
                daily_budget_cents=1000,
                ad_copies=[{"headline": "H1", "body": "B1"}, {"headline": "H2", "body": "B2"}],
                targeting={"psychographics": {"interests": ["AI"]}},
                link_url="https://example.com",
            )
            assert result is not None
            assert result["linkedin_campaign_id"] == "camp_001"
            assert result["total_creatives"] == 2
            assert result["status"] == "ACTIVE"

    async def test_push_unconfigured(self, li_unconfigured):
        result = await li_unconfigured.push_campaign(
            campaign_name="X", campaign_type="traffic", daily_budget_cents=500,
            ad_copies=[{"headline": "H", "body": "B"}], link_url="url",
        )
        assert result is None

    async def test_push_campaign_creation_fails(self, li_client):
        with patch.object(li_client, "create_campaign", AsyncMock(return_value=None)):
            result = await li_client.push_campaign(
                campaign_name="Fail", campaign_type="traffic", daily_budget_cents=500,
                ad_copies=[{"headline": "H", "body": "B"}], link_url="url",
            )
            assert result is None

    async def test_push_no_creatives(self, li_client):
        with patch.object(li_client, "create_campaign", AsyncMock(return_value={"id": "c1"})):
            with patch.object(li_client, "set_targeting", AsyncMock(return_value=None)):
                with patch.object(li_client, "create_creative", AsyncMock(return_value=None)):
                    result = await li_client.push_campaign(
                        campaign_name="Empty", campaign_type="traffic",
                        daily_budget_cents=500, ad_copies=[{"headline": "H", "body": "B"}],
                        link_url="url",
                    )
                    assert result is None


class TestLinkedInPerformance:
    async def test_get_analytics(self, li_client):
        resp = _mock_resp({
            "elements": [{
                "impressions": 3000, "clicks": 150,
                "costInLocalCurrency": 25.50, "externalWebsiteConversions": 8,
            }],
        })
        mock = _mock_client("get", resp)
        with patch("arclane.integrations.linkedin_ads_client.httpx.AsyncClient", return_value=mock):
            result = await li_client.get_campaign_analytics("camp_1")
            assert result["impressions"] == 3000
            assert result["clicks"] == 150
            assert result["spend_cents"] == 2550
            assert result["conversions"] == 8

    async def test_get_analytics_empty(self, li_client):
        resp = _mock_resp({"elements": []})
        mock = _mock_client("get", resp)
        with patch("arclane.integrations.linkedin_ads_client.httpx.AsyncClient", return_value=mock):
            assert await li_client.get_campaign_analytics("camp_1") is None

    async def test_get_analytics_unconfigured(self, li_unconfigured):
        assert await li_unconfigured.get_campaign_analytics("camp_1") is None


class TestLinkedInTargeting:
    def test_build_targeting_with_interests(self):
        criteria = LinkedInAdsClient._build_targeting_criteria({
            "psychographics": {"interests": ["AI", "SaaS"]},
        })
        assert "include" in criteria

    def test_build_targeting_with_location(self):
        criteria = LinkedInAdsClient._build_targeting_criteria({
            "demographics": {"location": "US"},
        })
        assert "include" in criteria

    def test_build_targeting_default(self):
        criteria = LinkedInAdsClient._build_targeting_criteria({})
        assert "include" in criteria  # Falls back to US

    def test_cta_mapping(self):
        assert LinkedInAdsClient._resolve_cta("Sign Up") == "SIGN_UP"
        assert LinkedInAdsClient._resolve_cta("download") == "DOWNLOAD"
        assert LinkedInAdsClient._resolve_cta(None) == "LEARN_MORE"
        assert LinkedInAdsClient._resolve_cta("random") == "LEARN_MORE"


class TestLinkedInMaps:
    def test_objectives(self):
        for t in ("awareness", "traffic", "conversion", "retargeting"):
            assert t in LI_OBJ_MAP


# ===========================================================================
# TWITTER/X ADS
# ===========================================================================


@pytest.fixture
def tw_client():
    return TwitterAdsClient(
        account_id="tw-acc-123", consumer_key="ck", consumer_secret="cs",
        access_token="at", access_secret="as",
    )


@pytest.fixture
def tw_unconfigured():
    return TwitterAdsClient(
        account_id="", consumer_key="", consumer_secret="",
        access_token="", access_secret="",
    )


class TestTwitterConfig:
    def test_configured(self, tw_client):
        assert tw_client.configured is True

    def test_unconfigured(self, tw_unconfigured):
        assert tw_unconfigured.configured is False


class TestTwitterCampaign:
    async def test_create_campaign(self, tw_client):
        # First call: funding instrument, second: create campaign
        fi_resp = _mock_resp({"data": [{"id": "fi_001", "entity_status": "ACTIVE"}]})
        camp_resp = _mock_resp({"data": {"id": "camp_tw_001"}})

        mock = AsyncMock()
        mock.get = AsyncMock(return_value=fi_resp)
        mock.post = AsyncMock(return_value=camp_resp)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.twitter_ads_client.httpx.AsyncClient", return_value=mock):
            result = await tw_client.create_campaign("Test TW", "traffic", 1000)
            assert result is not None
            assert result["id"] == "camp_tw_001"

    async def test_create_campaign_unconfigured(self, tw_unconfigured):
        assert await tw_unconfigured.create_campaign("Test") is None

    async def test_create_line_item(self, tw_client):
        resp = _mock_resp({"data": {"id": "li_001"}})
        mock = _mock_client("post", resp)
        with patch("arclane.integrations.twitter_ads_client.httpx.AsyncClient", return_value=mock):
            result = await tw_client.create_line_item("camp_1", "Test LI")
            assert result["id"] == "li_001"

    async def test_create_tweet(self, tw_client):
        resp = _mock_resp({"data": {"id": "tweet_001"}})
        mock = _mock_client("post", resp)
        with patch("arclane.integrations.twitter_ads_client.httpx.AsyncClient", return_value=mock):
            result = await tw_client.create_tweet("Check out our product!", "https://example.com")
            assert result["tweet_id"] == "tweet_001"

    async def test_create_promoted_tweet(self, tw_client):
        resp = _mock_resp({"data": [{"id": "pt_001"}]})
        mock = _mock_client("post", resp)
        with patch("arclane.integrations.twitter_ads_client.httpx.AsyncClient", return_value=mock):
            result = await tw_client.create_promoted_tweet("li_001", "tweet_001")
            assert result["id"] == "pt_001"


class TestTwitterPush:
    async def test_push_full_flow(self, tw_client):
        fi_resp = _mock_resp({"data": [{"id": "fi_001", "entity_status": "ACTIVE"}]})
        camp_resp = _mock_resp({"data": {"id": "camp_001"}})
        li_resp = _mock_resp({"data": {"id": "li_001"}})
        tweet_resp = _mock_resp({"data": {"id": "tweet_001"}})
        promo_resp = _mock_resp({"data": [{"id": "pt_001"}]})
        target_resp = _mock_resp({"data": {"id": "tc_001"}})
        status_resp = _mock_resp({"data": {"id": "camp_001"}})

        mock = AsyncMock()
        mock.get = AsyncMock(return_value=fi_resp)
        mock.post = AsyncMock(side_effect=[
            camp_resp, li_resp,    # campaign + line item
            tweet_resp, promo_resp, # tweet 1 + promote
            tweet_resp, promo_resp, # tweet 2 + promote
            target_resp,            # targeting
        ])
        mock.put = AsyncMock(return_value=status_resp)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.twitter_ads_client.httpx.AsyncClient", return_value=mock):
            result = await tw_client.push_campaign(
                campaign_name="TW Full", campaign_type="traffic",
                daily_budget_cents=1000,
                ad_copies=[{"headline": "H1", "body": "B1"}, {"headline": "H2", "body": "B2"}],
                targeting={"demographics": {"location": "US"}},
                link_url="https://example.com",
            )
            assert result is not None
            assert result["twitter_campaign_id"] == "camp_001"
            assert result["total_promoted"] == 2
            assert result["status"] == "ACTIVE"

    async def test_push_unconfigured(self, tw_unconfigured):
        result = await tw_unconfigured.push_campaign(
            campaign_name="X", campaign_type="traffic", daily_budget_cents=500,
            ad_copies=[{"headline": "H", "body": "B"}], link_url="url",
        )
        assert result is None

    async def test_push_campaign_fails(self, tw_client):
        with patch.object(tw_client, "create_campaign", AsyncMock(return_value=None)):
            result = await tw_client.push_campaign(
                campaign_name="Fail", campaign_type="traffic", daily_budget_cents=500,
                ad_copies=[{"headline": "H", "body": "B"}], link_url="url",
            )
            assert result is None

    async def test_push_no_promoted(self, tw_client):
        with patch.object(tw_client, "create_campaign", AsyncMock(return_value={"id": "c1"})):
            with patch.object(tw_client, "create_line_item", AsyncMock(return_value={"id": "li1"})):
                with patch.object(tw_client, "create_tweet", AsyncMock(return_value=None)):
                    result = await tw_client.push_campaign(
                        campaign_name="Empty", campaign_type="traffic",
                        daily_budget_cents=500, ad_copies=[{"headline": "H", "body": "B"}],
                        link_url="url",
                    )
                    assert result is None


class TestTwitterPerformance:
    async def test_get_stats(self, tw_client):
        resp = _mock_resp({
            "data": [{
                "id_data": [{
                    "metrics": {
                        "impressions": [6000],
                        "clicks": [300],
                        "billed_charge_local_micro": [400000000],  # $40
                        "conversion_purchases_order_quantity": [15],
                    }
                }],
            }],
        })
        mock = _mock_client("get", resp)
        with patch("arclane.integrations.twitter_ads_client.httpx.AsyncClient", return_value=mock):
            result = await tw_client.get_campaign_stats("camp_1")
            assert result["impressions"] == 6000
            assert result["clicks"] == 300
            assert result["spend_cents"] == 40000
            assert result["conversions"] == 15

    async def test_get_stats_empty(self, tw_client):
        resp = _mock_resp({"data": []})
        mock = _mock_client("get", resp)
        with patch("arclane.integrations.twitter_ads_client.httpx.AsyncClient", return_value=mock):
            assert await tw_client.get_campaign_stats("camp_1") is None

    async def test_get_stats_unconfigured(self, tw_unconfigured):
        assert await tw_unconfigured.get_campaign_stats("camp_1") is None


class TestTwitterTargeting:
    def test_location(self):
        criteria = TwitterAdsClient._build_targeting_criteria({
            "demographics": {"location": "US"},
        })
        assert any(c["targeting_type"] == "LOCATION" for c in criteria)

    def test_interests(self):
        criteria = TwitterAdsClient._build_targeting_criteria({
            "psychographics": {"interests": ["AI", "startups"]},
        })
        interest_criteria = [c for c in criteria if c["targeting_type"] == "INTEREST"]
        assert len(interest_criteria) == 2

    def test_gender(self):
        criteria = TwitterAdsClient._build_targeting_criteria({
            "demographics": {"gender": "female"},
        })
        assert any(c["targeting_type"] == "GENDER" and c["targeting_value"] == "2" for c in criteria)

    def test_segments(self):
        criteria = TwitterAdsClient._build_targeting_criteria({
            "segments": [{"psychographics": {"interests": ["tech"]}}],
        })
        assert any(c["targeting_type"] == "INTEREST" for c in criteria)

    def test_empty(self):
        assert TwitterAdsClient._build_targeting_criteria({}) == []


class TestTwitterMaps:
    def test_objectives(self):
        for t in ("awareness", "traffic", "conversion", "retargeting"):
            assert t in TW_OBJ_MAP


# ===========================================================================
# SERVICE WIRING — launch_campaign + sync
# ===========================================================================


class TestLaunchLinkedIn:
    async def test_launch_pushes_to_linkedin(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(business, db_session, name="LI Push", platform="linkedin")
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="professional",
        ))
        await db_session.flush()

        mock_li = AsyncMock(spec=LinkedInAdsClient)
        mock_li.configured = True
        mock_li.push_campaign = AsyncMock(return_value={
            "linkedin_campaign_id": "li_camp_001",
            "creatives": [{"creative_id": "cr_001"}],
            "total_creatives": 1, "status": "ACTIVE",
        })

        result = await launch_campaign(business, campaign.id, db_session, linkedin_client=mock_li)
        assert result["status"] == "active"
        assert "linkedin" in result
        assert result["linkedin"]["linkedin_campaign_id"] == "li_camp_001"

    async def test_launch_linkedin_unconfigured_skips(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(business, db_session, name="No LI", platform="linkedin")
        db_session.add(AdCopy(campaign_id=campaign.id, headline="H", body="B", platform_format="single_image", tone="professional"))
        await db_session.flush()

        mock_li = AsyncMock(spec=LinkedInAdsClient)
        mock_li.configured = False
        result = await launch_campaign(business, campaign.id, db_session, linkedin_client=mock_li)
        assert result["status"] == "active"
        assert "linkedin" not in result


class TestLaunchTwitter:
    async def test_launch_pushes_to_twitter(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(business, db_session, name="TW Push", platform="twitter")
        db_session.add(AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="text", tone="casual",
        ))
        await db_session.flush()

        mock_tw = AsyncMock(spec=TwitterAdsClient)
        mock_tw.configured = True
        mock_tw.push_campaign = AsyncMock(return_value={
            "twitter_campaign_id": "tw_camp_001",
            "twitter_line_item_id": "tw_li_001",
            "promoted_tweets": [{"promoted_tweet_id": "pt_001"}],
            "total_promoted": 1, "status": "ACTIVE",
        })

        result = await launch_campaign(business, campaign.id, db_session, twitter_client=mock_tw)
        assert result["status"] == "active"
        assert "twitter" in result
        assert result["twitter"]["twitter_campaign_id"] == "tw_camp_001"

    async def test_launch_twitter_unconfigured_skips(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(business, db_session, name="No TW", platform="twitter")
        db_session.add(AdCopy(campaign_id=campaign.id, headline="H", body="B", platform_format="text", tone="professional"))
        await db_session.flush()

        mock_tw = AsyncMock(spec=TwitterAdsClient)
        mock_tw.configured = False
        result = await launch_campaign(business, campaign.id, db_session, twitter_client=mock_tw)
        assert result["status"] == "active"
        assert "twitter" not in result


class TestSyncLinkedIn:
    async def test_sync_linkedin(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(business, db_session, name="Sync LI", platform="linkedin")
        campaign.metadata_json = {"linkedin": {"linkedin_campaign_id": "li_camp_001"}}
        await db_session.flush()

        mock_li = AsyncMock(spec=LinkedInAdsClient)
        mock_li.configured = True
        mock_li.sync_campaign_performance = AsyncMock(return_value={
            "impressions": 2000, "clicks": 100, "spend_cents": 1500,
            "cpc_cents": 15, "ctr": 5.0, "conversions": 5,
        })

        result = await sync_campaign_performance(
            business, campaign.id, db_session, linkedin_client=mock_li,
        )
        assert result["performance"]["impressions"] == 2000


class TestSyncTwitter:
    async def test_sync_twitter(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, sync_campaign_performance

        campaign = await create_campaign(business, db_session, name="Sync TW", platform="twitter")
        campaign.metadata_json = {"twitter": {"twitter_campaign_id": "tw_camp_001"}}
        await db_session.flush()

        mock_tw = AsyncMock(spec=TwitterAdsClient)
        mock_tw.configured = True
        mock_tw.sync_campaign_performance = AsyncMock(return_value={
            "impressions": 4000, "clicks": 200, "spend_cents": 2000,
            "cpc_cents": 10, "ctr": 5.0, "conversions": 8,
        })

        result = await sync_campaign_performance(
            business, campaign.id, db_session, twitter_client=mock_tw,
        )
        assert result["performance"]["clicks"] == 200
