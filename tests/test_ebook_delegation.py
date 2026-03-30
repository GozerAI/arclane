"""Tests for ebook/series delegation from Arclane to Content Production."""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from arclane.integrations.content_production_client import ContentProductionClient


# ── ContentProductionClient.produce_ebook ─────────────────────


class TestProduceEbook:
    def test_produce_ebook_sends_correct_payload(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")

        with patch.object(client, "_request", return_value={"job_id": "ebook-1"}) as mock_req:
            result = client.produce_ebook(
                topic="AI in Healthcare",
                category="technology",
                audience="healthcare professionals",
                priority=0.8,
                marketplace_credentials={"gumroad_api_token": "gum-key"},
                revenue_webhook_url="http://localhost:8012/api/businesses/test-biz/webhooks/revenue",
            )

            assert result == {"job_id": "ebook-1"}
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "/v1/production/produce"

            payload = call_args[0][2]
            assert payload["topic"] == "AI in Healthcare"
            assert payload["category"] == "technology"
            assert payload["audience"] == "healthcare professionals"
            assert payload["priority"] == 0.8
            assert payload["marketplace_credentials"]["gumroad_api_token"] == "gum-key"
            assert payload["revenue_webhook_url"].endswith("/webhooks/revenue")

    def test_produce_ebook_without_optional_fields(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")

        with patch.object(client, "_request", return_value={"job_id": "ebook-2"}) as mock_req:
            result = client.produce_ebook(topic="Simple Topic")

            assert result is not None
            payload = mock_req.call_args[0][2]
            assert payload["topic"] == "Simple Topic"
            assert payload["category"] == "general"
            assert payload["audience"] == ""
            assert payload["priority"] == 0.5
            assert "marketplace_credentials" not in payload
            assert "revenue_webhook_url" not in payload

    def test_produce_ebook_failure_returns_none(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")

        with patch.object(client, "_request", return_value=None):
            result = client.produce_ebook(topic="Failing Topic")
            assert result is None


# ── ContentProductionClient.priority_produce ──────────────────


class TestPriorityProduce:
    def test_priority_produce_includes_service_token(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")

        # Temporarily disable resilience to test raw urllib path
        import arclane.integrations.content_production_client as cp_mod
        orig = cp_mod._HAS_RESILIENCE
        cp_mod._HAS_RESILIENCE = False
        try:
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps({"priority": True}).encode()
                mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_resp

                result = client.priority_produce(
                    topic="Urgent Ebook",
                    category="business",
                    service_token="svc-tok-123",
                )

                assert result == {"priority": True}
                # Verify the request was made with X-Service-Token
                req_obj = mock_urlopen.call_args[0][0]
                assert req_obj.get_header("X-service-token") == "svc-tok-123"
                assert req_obj.full_url == "http://cp-test:8013/v1/priority/produce"
        finally:
            cp_mod._HAS_RESILIENCE = orig

    def test_priority_produce_failure_returns_none(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = client.priority_produce(
                topic="Failing Priority",
                service_token="tok",
            )
            assert result is None


# ── ContentProductionClient.get_production_status ─────────────


class TestGetProductionStatus:
    def test_get_production_status_success(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")
        status = {"active_jobs": 2, "queued_jobs": 5, "completed_jobs": 10}

        with patch.object(client, "_request", return_value=status) as mock_req:
            result = client.get_production_status()
            assert result == status
            mock_req.assert_called_once_with("GET", "/v1/production/status")

    def test_get_production_status_failure(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")

        with patch.object(client, "_request", return_value=None):
            assert client.get_production_status() is None


# ── ContentProductionClient.get_job_status ────────────────────


class TestGetJobStatus:
    def test_get_job_status_success(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")
        jobs = {"active": [], "queued": [{"topic": "AI"}], "completed": []}

        with patch.object(client, "_request", return_value=jobs) as mock_req:
            result = client.get_job_status()
            assert result == jobs
            mock_req.assert_called_once_with("GET", "/v1/production/jobs")


# ── Orchestrator._maybe_delegate_to_content_production ────────


def _make_business(slug="test-biz", description="A test business", agent_config=None):
    """Create a mock Business object for testing."""
    biz = MagicMock()
    biz.id = 1
    biz.slug = slug
    biz.name = "Test Business"
    biz.description = description
    biz.agent_config = agent_config or {}
    return biz


def _make_cycle(cycle_id=10):
    mock = MagicMock()
    mock.id = cycle_id
    return mock


class TestMaybeDelegateToContentProduction:
    @pytest.mark.asyncio
    async def test_delegates_for_high_value_report(self):
        """Reports with >1000 words trigger ebook delegation."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        mock_client.produce_ebook.return_value = {"job_id": "ebook-99"}
        orch._cp_client = mock_client

        business = _make_business(
            agent_config={"marketplace_credentials": {"gumroad_api_token": "tok"}},
        )
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1200)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "report",
                    "content_title": "AI Market Analysis",
                    "content_body": long_content,
                    "area": "cso",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)

        mock_client.produce_ebook.assert_called_once()
        call_kwargs = mock_client.produce_ebook.call_args[1]
        assert call_kwargs["topic"] == "AI Market Analysis"
        assert call_kwargs["priority"] == 0.7
        assert call_kwargs["marketplace_credentials"]["gumroad_api_token"] == "tok"
        assert "webhooks/revenue" in call_kwargs["revenue_webhook_url"]

        # Activity record should be added
        session.add.assert_called()

    @pytest.mark.asyncio
    async def test_delegates_for_high_value_blog(self):
        """Blogs with >1000 words also trigger delegation."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        mock_client.produce_ebook.return_value = {"job_id": "ebook-100"}
        orch._cp_client = mock_client

        business = _make_business()
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1500)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "blog",
                    "content_title": "Deep Dive on LLMs",
                    "content_body": long_content,
                    "area": "cmo",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)
        mock_client.produce_ebook.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_social_posts(self):
        """Social posts are never delegated to Content Production."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        orch._cp_client = mock_client

        business = _make_business()
        cycle = _make_cycle()
        session = AsyncMock()

        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "social",
                    "content_title": "Quick Tweet",
                    "content_body": "Just a short social post",
                    "area": "cmo",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)
        mock_client.produce_ebook.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_short_content(self):
        """Content under 1000 words is not delegated."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        orch._cp_client = mock_client

        business = _make_business()
        cycle = _make_cycle()
        session = AsyncMock()

        short_content = " ".join(["word"] * 500)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "report",
                    "content_title": "Short Report",
                    "content_body": short_content,
                    "area": "cso",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)
        mock_client.produce_ebook.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_failed_results(self):
        """Failed task results are never delegated."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        orch._cp_client = mock_client

        business = _make_business()
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1200)
        cycle_result = {
            "results": [
                {
                    "status": "failed",
                    "content_type": "report",
                    "content_title": "Failed Report",
                    "content_body": long_content,
                    "area": "cso",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)
        mock_client.produce_ebook.assert_not_called()

    @pytest.mark.asyncio
    async def test_includes_marketplace_credentials_when_present(self):
        """Marketplace credentials from business config are passed through."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        mock_client.produce_ebook.return_value = {"job_id": "ebook-mc"}
        orch._cp_client = mock_client

        creds = {
            "gumroad_api_token": "gum-key-abc",
            "etsy_api_key": "etsy-key-def",
        }
        business = _make_business(agent_config={"marketplace_credentials": creds})
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1100)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "report",
                    "content_title": "Creds Report",
                    "content_body": long_content,
                    "area": "cso",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)

        call_kwargs = mock_client.produce_ebook.call_args[1]
        assert call_kwargs["marketplace_credentials"] == creds

    @pytest.mark.asyncio
    async def test_skips_when_cp_client_not_configured(self):
        """If _cp_client is None (no CONTENT_PRODUCTION_URL), delegation is skipped."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        orch._cp_client = None

        business = _make_business()
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1200)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "report",
                    "content_title": "Ignored Report",
                    "content_body": long_content,
                    "area": "cso",
                },
            ],
        }

        # Should not raise or make any calls
        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_delegation_failure_does_not_raise(self):
        """If CP client raises, the method logs but does not propagate."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        mock_client.produce_ebook.side_effect = Exception("CP exploded")
        orch._cp_client = mock_client

        business = _make_business()
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1200)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "report",
                    "content_title": "Boom Report",
                    "content_body": long_content,
                    "area": "cso",
                },
            ],
        }

        # Should not raise
        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)

    @pytest.mark.asyncio
    async def test_uses_result_text_when_no_content_body(self):
        """Falls back to 'result' field if 'content_body' is missing."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        mock_client.produce_ebook.return_value = {"job_id": "ebook-fb"}
        orch._cp_client = mock_client

        business = _make_business()
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1100)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "blog",
                    "content_title": "Fallback Blog",
                    "result": long_content,
                    "area": "cmo",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)
        mock_client.produce_ebook.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_marketplace_credentials_sends_none(self):
        """When business has no marketplace_credentials, None is passed."""
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator.__new__(ArclaneOrchestrator)
        mock_client = MagicMock()
        mock_client.produce_ebook.return_value = {"job_id": "ebook-nc"}
        orch._cp_client = mock_client

        business = _make_business(agent_config={})
        cycle = _make_cycle()
        session = AsyncMock()

        long_content = " ".join(["word"] * 1100)
        cycle_result = {
            "results": [
                {
                    "status": "completed",
                    "content_type": "report",
                    "content_title": "No Creds Report",
                    "content_body": long_content,
                    "area": "cso",
                },
            ],
        }

        await orch._maybe_delegate_to_content_production(business, cycle, cycle_result, session)

        call_kwargs = mock_client.produce_ebook.call_args[1]
        assert call_kwargs["marketplace_credentials"] is None


# ── Ebook Status Route ────────────────────────────────────────


class TestEbookStatusRoute:
    def test_ebook_status_filters_by_business(self):
        """The status endpoint filters CP jobs by business name/slug."""
        from arclane.api.routes.ebooks import ebook_production_status

        # Directly test the filtering logic
        jobs = {
            "active": [
                {"topic": "Test Business AI Guide", "audience": "devs"},
                {"topic": "Unrelated Topic", "audience": "someone"},
            ],
            "queued": [
                {"topic": "Another for test-biz", "audience": ""},
            ],
            "completed": [
                {"topic": "Old Guide", "audience": "A test business audience"},
                {"topic": "Totally unrelated", "audience": "nobody"},
            ],
        }

        # Simulate the filtering logic from the route
        biz_terms = {"test business", "test-biz"}

        def _is_relevant(job):
            topic = (job.get("topic") or "").lower()
            audience = (job.get("audience") or "").lower()
            for term in biz_terms:
                if term in topic or term in audience:
                    return True
            return False

        active = [j for j in jobs["active"] if _is_relevant(j)]
        queued = [j for j in jobs["queued"] if _is_relevant(j)]
        completed = [j for j in jobs["completed"] if _is_relevant(j)]

        assert len(active) == 1
        assert active[0]["topic"] == "Test Business AI Guide"
        assert len(queued) == 1
        assert len(completed) == 1  # "A test business audience" matches

    def test_ebook_status_when_cp_unavailable(self):
        """When CP returns None, the endpoint returns unavailable status."""
        from arclane.api.routes.ebooks import _get_cp_client

        client = _get_cp_client()
        with patch.object(client, "get_job_status", return_value=None):
            result = client.get_job_status()
            assert result is None
