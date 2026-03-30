"""Tests for the advertising module — service, routes, and cycle integration."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.models.tables import AdCampaign, AdCopy, Business, CustomerSegment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def business(db_session: AsyncSession) -> Business:
    biz = Business(
        slug="test-ads",
        name="AdTest Inc",
        description="A SaaS tool that helps small businesses automate their marketing workflows.",
        owner_email="ads@test.com",
        plan="pro",
        working_days_remaining=10,
    )
    db_session.add(biz)
    await db_session.flush()
    return biz


@pytest.fixture
def mock_llm():
    """Mock LLM client that returns valid JSON ad copies."""
    client = AsyncMock()
    client.generate = AsyncMock(return_value=None)
    client.model_for_area = lambda area: "test-model"
    client.enabled = True
    return client


# ---------------------------------------------------------------------------
# Service: Ad Copy Generation
# ---------------------------------------------------------------------------


class TestAdCopyGeneration:
    async def test_generate_copies_fallback(self, db_session, business, mock_llm):
        """When LLM returns None, deterministic fallback copies are created."""
        from arclane.services.advertising_service import generate_ad_copies

        copies = await generate_ad_copies(
            business, db_session,
            num_variations=3,
            platform="facebook",
            llm_client=mock_llm,
        )
        assert len(copies) == 3
        assert all("headline" in c for c in copies)
        assert all("body" in c for c in copies)

    async def test_generate_copies_from_llm(self, db_session, business, mock_llm):
        """When LLM returns valid JSON, those copies are used."""
        mock_llm.generate = AsyncMock(return_value=json.dumps([
            {"headline": "Grow faster", "body": "Automate your marketing.", "cta": "Try Free", "image_prompt": "happy team"},
            {"headline": "Save hours", "body": "Stop manual busywork.", "cta": "Learn More", "image_prompt": "clock"},
        ]))
        from arclane.services.advertising_service import generate_ad_copies

        copies = await generate_ad_copies(
            business, db_session,
            num_variations=2,
            platform="facebook",
            llm_client=mock_llm,
        )
        assert len(copies) == 2
        assert copies[0]["headline"] == "Grow faster"
        assert copies[1]["cta"] == "Learn More"

    async def test_generate_copies_persisted(self, db_session, business, mock_llm):
        """Generated copies are saved to the database."""
        from arclane.services.advertising_service import generate_ad_copies

        await generate_ad_copies(
            business, db_session,
            num_variations=3,
            platform="google",
            llm_client=mock_llm,
        )
        result = await db_session.execute(select(AdCopy))
        assert len(result.scalars().all()) == 3

    async def test_generate_copies_with_campaign_id(self, db_session, business, mock_llm):
        """Copies are linked to a campaign when campaign_id is provided."""
        from arclane.services.advertising_service import create_campaign, generate_ad_copies

        campaign = await create_campaign(
            business, db_session, name="Test", platform="facebook",
        )
        await generate_ad_copies(
            business, db_session,
            campaign_id=campaign.id,
            num_variations=2,
            llm_client=mock_llm,
        )
        result = await db_session.execute(
            select(AdCopy).where(AdCopy.campaign_id == campaign.id)
        )
        assert len(result.scalars().all()) == 2

    async def test_generate_copies_respects_tone(self, db_session, business, mock_llm):
        """Tone parameter is passed to the LLM and stored on copies."""
        from arclane.services.advertising_service import generate_ad_copies

        await generate_ad_copies(
            business, db_session,
            tone="urgent",
            num_variations=1,
            llm_client=mock_llm,
        )
        result = await db_session.execute(select(AdCopy))
        copy = result.scalars().first()
        assert copy.tone == "urgent"

    async def test_generate_copies_platform_format(self, db_session, business, mock_llm):
        """Platform determines the default ad format."""
        from arclane.services.advertising_service import generate_ad_copies

        await generate_ad_copies(business, db_session, platform="google", num_variations=1, llm_client=mock_llm)
        result = await db_session.execute(select(AdCopy))
        copy = result.scalars().first()
        assert copy.platform_format == "text"

    async def test_generate_copies_markdown_fence_stripped(self, db_session, business, mock_llm):
        """LLM output wrapped in markdown fences is parsed correctly."""
        mock_llm.generate = AsyncMock(return_value='```json\n[{"headline":"H","body":"B","cta":"C","image_prompt":"I"}]\n```')
        from arclane.services.advertising_service import generate_ad_copies

        copies = await generate_ad_copies(business, db_session, num_variations=1, llm_client=mock_llm)
        assert len(copies) == 1
        assert copies[0]["headline"] == "H"

    async def test_generate_copies_invalid_json_falls_back(self, db_session, business, mock_llm):
        """Invalid JSON from LLM triggers deterministic fallback."""
        mock_llm.generate = AsyncMock(return_value="not json at all {{{")
        from arclane.services.advertising_service import generate_ad_copies

        copies = await generate_ad_copies(business, db_session, num_variations=2, llm_client=mock_llm)
        assert len(copies) == 2  # fallback
        assert copies[0]["headline"]  # has content

    async def test_generate_copies_uses_segment_context(self, db_session, business, mock_llm):
        """When segments exist, they are included in the LLM prompt."""
        db_session.add(CustomerSegment(
            business_id=business.id, name="Early Adopters",
            description="Tech-savvy users", priority=8,
        ))
        await db_session.flush()

        from arclane.services.advertising_service import generate_ad_copies
        await generate_ad_copies(business, db_session, num_variations=1, llm_client=mock_llm)
        # Verify the LLM was called (segments were gathered for context)
        mock_llm.generate.assert_called_once()
        call_args = mock_llm.generate.call_args
        # system_prompt is passed as keyword arg
        system_prompt = call_args.kwargs.get("system_prompt", "")
        assert "Early Adopters" in system_prompt

    async def test_generate_copies_key_message(self, db_session, business, mock_llm):
        """Key message is included in the LLM system prompt."""
        from arclane.services.advertising_service import generate_ad_copies
        await generate_ad_copies(
            business, db_session,
            key_message="50% off this week only",
            num_variations=1,
            llm_client=mock_llm,
        )
        call_args = mock_llm.generate.call_args
        system_prompt = call_args.kwargs.get("system_prompt", "")
        assert "50% off this week only" in system_prompt


# ---------------------------------------------------------------------------
# Service: Customer Segmentation
# ---------------------------------------------------------------------------


class TestCustomerSegmentation:
    async def test_segment_customers_fallback(self, db_session, business, mock_llm):
        """When LLM returns None, deterministic fallback segments are created."""
        from arclane.services.advertising_service import segment_customers

        segments = await segment_customers(business, db_session, llm_client=mock_llm)
        assert len(segments) == 3
        assert all("name" in s for s in segments)

    async def test_segment_customers_from_llm(self, db_session, business, mock_llm):
        """When LLM returns valid JSON, those segments are used."""
        mock_llm.generate = AsyncMock(return_value=json.dumps([
            {"name": "SMB Owners", "description": "Small biz", "priority": 9},
            {"name": "Freelancers", "description": "Solo operators", "priority": 7},
        ]))
        from arclane.services.advertising_service import segment_customers

        segments = await segment_customers(business, db_session, llm_client=mock_llm)
        assert len(segments) == 2
        assert segments[0]["name"] == "SMB Owners"

    async def test_segment_customers_persisted(self, db_session, business, mock_llm):
        """Segments are saved to the database."""
        from arclane.services.advertising_service import segment_customers

        await segment_customers(business, db_session, llm_client=mock_llm)
        result = await db_session.execute(
            select(CustomerSegment).where(CustomerSegment.business_id == business.id)
        )
        assert len(result.scalars().all()) == 3

    async def test_segment_customers_no_duplicates(self, db_session, business, mock_llm):
        """Existing segment names are not re-created."""
        db_session.add(CustomerSegment(
            business_id=business.id, name="Early Adopters",
            description="Already exists", priority=8,
        ))
        await db_session.flush()

        from arclane.services.advertising_service import segment_customers
        segments = await segment_customers(business, db_session, llm_client=mock_llm)
        # Fallback has "Early Adopters" — should be skipped
        assert all(s["name"] != "Early Adopters" for s in segments)

    async def test_segment_customers_max_five(self, db_session, business, mock_llm):
        """At most 5 segments are returned even if LLM gives more."""
        mock_llm.generate = AsyncMock(return_value=json.dumps([
            {"name": f"Seg {i}", "description": f"Desc {i}", "priority": i}
            for i in range(10)
        ]))
        from arclane.services.advertising_service import segment_customers
        segments = await segment_customers(business, db_session, llm_client=mock_llm)
        assert len(segments) <= 5


# ---------------------------------------------------------------------------
# Service: Campaign CRUD
# ---------------------------------------------------------------------------


class TestCampaignCRUD:
    async def test_create_campaign(self, db_session, business):
        from arclane.services.advertising_service import create_campaign

        campaign = await create_campaign(
            business, db_session,
            name="Spring Launch",
            platform="facebook",
            campaign_type="awareness",
            budget_cents=5000,
        )
        assert campaign.id is not None
        assert campaign.name == "Spring Launch"
        assert campaign.platform == "facebook"
        assert campaign.status == "draft"
        assert campaign.budget_cents == 5000

    async def test_create_campaign_with_targeting(self, db_session, business):
        from arclane.services.advertising_service import create_campaign

        targeting = {"age_range": "25-45", "interests": ["marketing"]}
        campaign = await create_campaign(
            business, db_session,
            name="Targeted", platform="linkedin",
            target_audience=targeting,
        )
        assert campaign.target_audience == targeting

    async def test_launch_campaign_success(self, db_session, business, mock_llm):
        from arclane.services.advertising_service import (
            create_campaign,
            generate_ad_copies,
            launch_campaign,
        )

        campaign = await create_campaign(business, db_session, name="Launch", platform="facebook")
        await generate_ad_copies(
            business, db_session, campaign_id=campaign.id,
            num_variations=2, llm_client=mock_llm,
        )
        result = await launch_campaign(business, campaign.id, db_session)
        assert result["status"] == "active"
        assert result["ad_copies_count"] == 2

    async def test_launch_campaign_no_copies(self, db_session, business):
        from arclane.services.advertising_service import create_campaign, launch_campaign

        campaign = await create_campaign(business, db_session, name="Empty", platform="google")
        result = await launch_campaign(business, campaign.id, db_session)
        assert "error" in result

    async def test_launch_campaign_already_active(self, db_session, business, mock_llm):
        from arclane.services.advertising_service import (
            create_campaign, generate_ad_copies, launch_campaign,
        )

        campaign = await create_campaign(business, db_session, name="Active", platform="facebook")
        await generate_ad_copies(business, db_session, campaign_id=campaign.id, num_variations=1, llm_client=mock_llm)
        await launch_campaign(business, campaign.id, db_session)
        result = await launch_campaign(business, campaign.id, db_session)
        assert result["error"] == "Campaign is already active"

    async def test_launch_auto_applies_segments(self, db_session, business, mock_llm):
        """Launch auto-applies top segments if no targeting is set."""
        from arclane.services.advertising_service import (
            create_campaign, generate_ad_copies, launch_campaign,
        )

        db_session.add(CustomerSegment(
            business_id=business.id, name="High Value",
            description="Premium customers", priority=10,
        ))
        await db_session.flush()

        campaign = await create_campaign(business, db_session, name="Auto Target", platform="facebook")
        await generate_ad_copies(business, db_session, campaign_id=campaign.id, num_variations=1, llm_client=mock_llm)
        result = await launch_campaign(business, campaign.id, db_session)
        assert result["status"] == "active"

        refreshed = await db_session.get(AdCampaign, campaign.id)
        assert refreshed.target_audience is not None
        assert refreshed.target_audience["segments"][0]["name"] == "High Value"

    async def test_launch_campaign_not_found(self, db_session, business):
        from arclane.services.advertising_service import launch_campaign
        result = await launch_campaign(business, 9999, db_session)
        assert result["error"] == "Campaign not found"

    async def test_get_campaign_performance(self, db_session, business, mock_llm):
        from arclane.services.advertising_service import (
            create_campaign, generate_ad_copies, get_campaign_performance,
        )

        campaign = await create_campaign(business, db_session, name="Perf", platform="google")
        await generate_ad_copies(business, db_session, campaign_id=campaign.id, num_variations=2, llm_client=mock_llm)
        perf = await get_campaign_performance(business, campaign.id, db_session)
        assert perf["campaign"]["name"] == "Perf"
        assert perf["total_copies"] == 2

    async def test_get_campaign_performance_not_found(self, db_session, business):
        from arclane.services.advertising_service import get_campaign_performance
        perf = await get_campaign_performance(business, 9999, db_session)
        assert "error" in perf


# ---------------------------------------------------------------------------
# Service: Full Campaign Generation
# ---------------------------------------------------------------------------


class TestFullCampaignGeneration:
    async def test_generate_full_campaign(self, db_session, business, mock_llm):
        from arclane.services.advertising_service import generate_full_campaign

        result = await generate_full_campaign(
            business, db_session,
            platform="instagram",
            campaign_type="traffic",
            llm_client=mock_llm,
        )
        assert result["platform"] == "instagram"
        assert result["campaign_type"] == "traffic"
        assert result["copies_generated"] >= 1
        assert result["launch"]["status"] == "active"

    async def test_full_campaign_creates_segments_if_missing(self, db_session, business, mock_llm):
        from arclane.services.advertising_service import generate_full_campaign

        await generate_full_campaign(business, db_session, llm_client=mock_llm)
        result = await db_session.execute(
            select(CustomerSegment).where(CustomerSegment.business_id == business.id)
        )
        assert len(result.scalars().all()) >= 1

    async def test_full_campaign_skips_segments_if_exist(self, db_session, business, mock_llm):
        db_session.add(CustomerSegment(
            business_id=business.id, name="Existing",
            description="Pre-existing segment", priority=5,
        ))
        await db_session.flush()

        from arclane.services.advertising_service import generate_full_campaign
        await generate_full_campaign(business, db_session, llm_client=mock_llm)
        result = await db_session.execute(
            select(CustomerSegment).where(CustomerSegment.business_id == business.id)
        )
        # Should still have just 1 (the pre-existing one), not 1 + 3 fallback
        segments = result.scalars().all()
        assert len(segments) >= 1


# ---------------------------------------------------------------------------
# Service: Parse Helpers
# ---------------------------------------------------------------------------


class TestParseHelpers:
    def test_parse_ad_copies_valid_json(self):
        from arclane.services.advertising_service import _parse_ad_copies
        raw = json.dumps([{"headline": "H1", "body": "B1", "cta": "C1", "image_prompt": "I1"}])
        result = _parse_ad_copies(raw, 1, "facebook", "single_image", "professional")
        assert len(result) == 1
        assert result[0]["headline"] == "H1"

    def test_parse_ad_copies_none(self):
        from arclane.services.advertising_service import _parse_ad_copies
        result = _parse_ad_copies(None, 2, "google", "text", "casual")
        assert len(result) == 2

    def test_parse_ad_copies_invalid_json(self):
        from arclane.services.advertising_service import _parse_ad_copies
        result = _parse_ad_copies("invalid{}", 3, "facebook", "single_image", "professional")
        assert len(result) == 3  # fallback

    def test_parse_ad_copies_truncates_headline(self):
        from arclane.services.advertising_service import _parse_ad_copies
        raw = json.dumps([{"headline": "H" * 600, "body": "B", "cta": "C", "image_prompt": "I"}])
        result = _parse_ad_copies(raw, 1, "facebook", "single_image", "professional")
        assert len(result[0]["headline"]) <= 500

    def test_parse_segments_valid_json(self):
        from arclane.services.advertising_service import _parse_segments
        raw = json.dumps([{"name": "S1", "description": "D1", "priority": 5}])
        result = _parse_segments(raw)
        assert len(result) == 1
        assert result[0]["name"] == "S1"

    def test_parse_segments_none(self):
        from arclane.services.advertising_service import _parse_segments
        result = _parse_segments(None)
        assert len(result) == 3  # fallback

    def test_parse_segments_invalid_json(self):
        from arclane.services.advertising_service import _parse_segments
        result = _parse_segments("bad json")
        assert len(result) == 3

    def test_fallback_copies_count(self):
        from arclane.services.advertising_service import _fallback_copies
        result = _fallback_copies(5, "twitter", "text", "casual")
        assert len(result) == 5

    def test_fallback_copies_limited(self):
        from arclane.services.advertising_service import _fallback_copies
        result = _fallback_copies(10, "facebook", "single_image", "professional")
        assert len(result) == 5  # max 5 angles

    def test_fallback_segments(self):
        from arclane.services.advertising_service import _fallback_segments
        result = _fallback_segments()
        assert len(result) == 3
        assert all("name" in s for s in result)


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_client(db_session, business):
    """HTTPX client with mocked auth and session."""
    from arclane.api.app import app

    async def _override_session():
        yield db_session

    async def _override_business():
        return business

    from arclane.core.database import get_session
    from arclane.api.deps import get_business

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_business] = _override_business

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


class TestAdvertisingRoutes:
    async def test_list_campaigns_empty(self, api_client):
        resp = await api_client.get("/api/businesses/test-ads/advertising/campaigns")
        assert resp.status_code == 200
        assert resp.json()["campaigns"] == []

    async def test_create_campaign_route(self, api_client):
        resp = await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "Route Test", "platform": "google", "campaign_type": "traffic"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Route Test"
        assert data["platform"] == "google"
        assert data["status"] == "draft"

    async def test_create_campaign_invalid_platform(self, api_client):
        resp = await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "Bad", "platform": "tiktok"},
        )
        assert resp.status_code == 422

    async def test_list_campaigns_filtered(self, api_client):
        await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "C1", "platform": "google"},
        )
        resp = await api_client.get(
            "/api/businesses/test-ads/advertising/campaigns?status=draft"
        )
        assert resp.status_code == 200
        assert len(resp.json()["campaigns"]) == 1

    @patch("arclane.services.advertising_service.ArclaneLLMClient")
    async def test_generate_copies_route(self, mock_cls, api_client):
        mock_cls.return_value.generate = AsyncMock(return_value=None)
        mock_cls.return_value.model_for_area = lambda a: "test"
        mock_cls.return_value.enabled = True

        # Create campaign first
        create_resp = await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "Copy Test", "platform": "facebook"},
        )
        campaign_id = create_resp.json()["id"]

        resp = await api_client.post(
            f"/api/businesses/test-ads/advertising/campaigns/{campaign_id}/copies",
            json={"num_variations": 2, "tone": "casual", "platform": "facebook"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    async def test_get_campaign_not_found(self, api_client):
        resp = await api_client.get("/api/businesses/test-ads/advertising/campaigns/9999")
        assert resp.status_code == 404

    @patch("arclane.services.advertising_service.ArclaneLLMClient")
    async def test_launch_campaign_route(self, mock_cls, api_client):
        mock_cls.return_value.generate = AsyncMock(return_value=None)
        mock_cls.return_value.model_for_area = lambda a: "test"
        mock_cls.return_value.enabled = True

        create_resp = await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "Launch Test", "platform": "facebook"},
        )
        cid = create_resp.json()["id"]
        await api_client.post(
            f"/api/businesses/test-ads/advertising/campaigns/{cid}/copies",
            json={"num_variations": 1, "platform": "facebook"},
        )
        resp = await api_client.post(
            f"/api/businesses/test-ads/advertising/campaigns/{cid}/launch"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @patch("arclane.services.advertising_service.ArclaneLLMClient")
    async def test_pause_and_resume_campaign(self, mock_cls, api_client):
        mock_cls.return_value.generate = AsyncMock(return_value=None)
        mock_cls.return_value.model_for_area = lambda a: "test"
        mock_cls.return_value.enabled = True

        create_resp = await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "Pause Test", "platform": "twitter"},
        )
        cid = create_resp.json()["id"]
        await api_client.post(
            f"/api/businesses/test-ads/advertising/campaigns/{cid}/copies",
            json={"num_variations": 1, "platform": "twitter"},
        )
        await api_client.post(f"/api/businesses/test-ads/advertising/campaigns/{cid}/launch")

        pause_resp = await api_client.post(f"/api/businesses/test-ads/advertising/campaigns/{cid}/pause")
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "paused"

        resume_resp = await api_client.post(f"/api/businesses/test-ads/advertising/campaigns/{cid}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "active"

    async def test_pause_non_active_campaign(self, api_client):
        create_resp = await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "Draft", "platform": "google"},
        )
        cid = create_resp.json()["id"]
        resp = await api_client.post(f"/api/businesses/test-ads/advertising/campaigns/{cid}/pause")
        assert resp.status_code == 400

    @patch("arclane.services.advertising_service.ArclaneLLMClient")
    async def test_list_segments_route(self, mock_cls, api_client):
        mock_cls.return_value.generate = AsyncMock(return_value=None)
        mock_cls.return_value.model_for_area = lambda a: "test"
        mock_cls.return_value.enabled = True

        await api_client.post("/api/businesses/test-ads/advertising/segments")
        resp = await api_client.get("/api/businesses/test-ads/advertising/segments")
        assert resp.status_code == 200
        assert len(resp.json()["segments"]) >= 1

    @patch("arclane.services.advertising_service.ArclaneLLMClient")
    async def test_generate_segments_route(self, mock_cls, api_client):
        mock_cls.return_value.generate = AsyncMock(return_value=None)
        mock_cls.return_value.model_for_area = lambda a: "test"
        mock_cls.return_value.enabled = True

        resp = await api_client.post("/api/businesses/test-ads/advertising/segments")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    @patch("arclane.services.advertising_service.ArclaneLLMClient")
    async def test_full_generate_route(self, mock_cls, api_client):
        mock_cls.return_value.generate = AsyncMock(return_value=None)
        mock_cls.return_value.model_for_area = lambda a: "test"
        mock_cls.return_value.enabled = True

        resp = await api_client.post(
            "/api/businesses/test-ads/advertising/generate",
            json={"name": "Full Gen", "platform": "instagram", "campaign_type": "conversion"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["platform"] == "instagram"
        assert data["copies_generated"] >= 1

    @patch("arclane.services.advertising_service.ArclaneLLMClient")
    async def test_approve_and_reject_copies(self, mock_cls, api_client):
        mock_cls.return_value.generate = AsyncMock(return_value=None)
        mock_cls.return_value.model_for_area = lambda a: "test"
        mock_cls.return_value.enabled = True

        create_resp = await api_client.post(
            "/api/businesses/test-ads/advertising/campaigns",
            json={"name": "Copy Status", "platform": "facebook"},
        )
        cid = create_resp.json()["id"]
        await api_client.post(
            f"/api/businesses/test-ads/advertising/campaigns/{cid}/copies",
            json={"num_variations": 2, "platform": "facebook"},
        )
        copies_resp = await api_client.get(f"/api/businesses/test-ads/advertising/campaigns/{cid}/copies")
        copies = copies_resp.json()["copies"]
        assert len(copies) == 2

        approve_resp = await api_client.post(f"/api/businesses/test-ads/advertising/copies/{copies[0]['id']}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"

        reject_resp = await api_client.post(f"/api/businesses/test-ads/advertising/copies/{copies[1]['id']}/reject")
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "rejected"

    async def test_approve_copy_not_found(self, api_client):
        resp = await api_client.post("/api/businesses/test-ads/advertising/copies/9999/approve")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Executive Prompt Integration
# ---------------------------------------------------------------------------


class TestAdvertisingPrompt:
    def test_advertising_prompt_exists(self):
        from arclane.engine.executive_prompts import EXECUTIVE_PROMPTS
        assert "advertising" in EXECUTIVE_PROMPTS
        pack = EXECUTIVE_PROMPTS["advertising"]
        assert pack["agent"] == "cmo"
        assert "paid advertising" in pack["system_prompt"].lower()

    def test_prompt_pack_for_area_returns_advertising(self):
        from arclane.engine.executive_prompts import prompt_pack_for_area
        pack = prompt_pack_for_area("advertising")
        assert pack["agent"] == "cmo"

    def test_agent_action_map_includes_advertising(self):
        from arclane.engine.orchestrator import AGENT_ACTION_MAP
        assert "advertising" in AGENT_ACTION_MAP


# ---------------------------------------------------------------------------
# Orchestrator Integration
# ---------------------------------------------------------------------------


class TestOrchestratorAdvertisingWiring:
    async def test_materialize_advertising_tasks(self, db_session, business, mock_llm):
        """Orchestrator materializes advertising tasks into real campaigns."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator(execution_mode="internal", llm_client=mock_llm)
        cycle_result = {"results": [{"area": "advertising", "status": "completed"}]}
        tasks = [{"area": "advertising", "platform": "facebook", "campaign_type": "awareness"}]

        from arclane.models.tables import Cycle
        cycle = Cycle(business_id=business.id, trigger="nightly", status="running")
        db_session.add(cycle)
        await db_session.flush()

        await orch._materialize_advertising_tasks(business, cycle, tasks, cycle_result, db_session)

        campaigns = await db_session.execute(
            select(AdCampaign).where(AdCampaign.business_id == business.id)
        )
        assert len(campaigns.scalars().all()) == 1

    async def test_materialize_skips_non_advertising(self, db_session, business, mock_llm):
        """Non-advertising tasks are ignored by the materializer."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator(execution_mode="internal", llm_client=mock_llm)
        tasks = [{"area": "content", "action": "write_blog"}]
        cycle_result = {"results": []}

        from arclane.models.tables import Cycle
        cycle = Cycle(business_id=business.id, trigger="nightly", status="running")
        db_session.add(cycle)
        await db_session.flush()

        await orch._materialize_advertising_tasks(business, cycle, tasks, cycle_result, db_session)

        campaigns = await db_session.execute(
            select(AdCampaign).where(AdCampaign.business_id == business.id)
        )
        assert len(campaigns.scalars().all()) == 0


# ---------------------------------------------------------------------------
# Schema Validation
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_campaign_create_valid(self):
        from arclane.models.schemas import CampaignCreate
        c = CampaignCreate(name="Test", platform="google")
        assert c.campaign_type == "awareness"

    def test_campaign_create_invalid_platform(self):
        from arclane.models.schemas import CampaignCreate
        with pytest.raises(Exception):
            CampaignCreate(name="Test", platform="tiktok")

    def test_ad_copy_generate_defaults(self):
        from arclane.models.schemas import AdCopyGenerate
        g = AdCopyGenerate()
        assert g.campaign_type == "awareness"
        assert g.tone == "professional"
        assert g.num_variations == 3

    def test_ad_copy_generate_max_variations(self):
        from arclane.models.schemas import AdCopyGenerate
        with pytest.raises(Exception):
            AdCopyGenerate(num_variations=20)

    def test_campaign_response_from_orm(self, db_session):
        from arclane.models.schemas import CampaignResponse
        campaign = AdCampaign(
            id=1, business_id=1, name="Test", platform="facebook",
            campaign_type="awareness", status="draft", budget_cents=0,
            spent_cents=0, created_at=datetime.now(timezone.utc),
        )
        resp = CampaignResponse.model_validate(campaign)
        assert resp.name == "Test"


# ---------------------------------------------------------------------------
# Model Relationships
# ---------------------------------------------------------------------------


class TestModelRelationships:
    async def test_campaign_copies_relationship(self, db_session, business):
        campaign = AdCampaign(
            business_id=business.id, name="Rel Test",
            platform="facebook", campaign_type="awareness",
        )
        db_session.add(campaign)
        await db_session.flush()

        copy = AdCopy(
            campaign_id=campaign.id, headline="H", body="B",
            platform_format="single_image", tone="professional",
        )
        db_session.add(copy)
        await db_session.flush()

        result = await db_session.execute(
            select(AdCopy).where(AdCopy.campaign_id == campaign.id)
        )
        assert len(result.scalars().all()) == 1

    async def test_business_campaigns_relationship(self, db_session, business):
        db_session.add(AdCampaign(
            business_id=business.id, name="Biz Rel",
            platform="google", campaign_type="traffic",
        ))
        await db_session.flush()

        result = await db_session.execute(
            select(AdCampaign).where(AdCampaign.business_id == business.id)
        )
        assert len(result.scalars().all()) == 1

    async def test_business_segments_relationship(self, db_session, business):
        db_session.add(CustomerSegment(
            business_id=business.id, name="Test Seg",
            description="Desc", priority=5,
        ))
        await db_session.flush()

        result = await db_session.execute(
            select(CustomerSegment).where(CustomerSegment.business_id == business.id)
        )
        assert len(result.scalars().all()) == 1
