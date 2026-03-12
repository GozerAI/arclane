"""Tests for Nexus integration — publishing insights and fetching knowledge."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from arclane.integrations.nexus_publisher import NexusPublisher


class TestPublishCycleInsights:
    @pytest.fixture
    def publisher(self):
        return NexusPublisher(base_url="http://nexus-test:8008")

    @pytest.mark.asyncio
    async def test_publishes_strategy_insights(self, publisher):
        results = [
            {"area": "strategy", "status": "completed", "result": "Market analysis shows growth in AI sector"},
            {"area": "content", "status": "completed", "result": "Blog post created", "content_type": "blog", "content_body": "..."},
        ]

        with patch("arclane.integrations.nexus_publisher.resilient_request",
                    new_callable=AsyncMock, return_value={"id": "k-1"}):
            published = await publisher.publish_cycle_insights("TestBiz", "AI startup", results)
            assert len(published) == 1  # only strategy, not content
            assert published[0]["area"] == "strategy"

    @pytest.mark.asyncio
    async def test_skips_failed_results(self, publisher):
        results = [{"area": "strategy", "status": "failed", "result": "error"}]
        published = await publisher.publish_cycle_insights("Biz", "desc", results)
        assert published == []

    @pytest.mark.asyncio
    async def test_skips_non_knowledge_areas(self, publisher):
        results = [
            {"area": "content", "status": "completed", "result": "blog written"},
            {"area": "engineering", "status": "completed", "result": "feature built"},
        ]
        published = await publisher.publish_cycle_insights("Biz", "desc", results)
        assert published == []

    @pytest.mark.asyncio
    async def test_graceful_failure(self, publisher):
        results = [{"area": "strategy", "status": "completed", "result": "analysis"}]
        with patch("arclane.integrations.nexus_publisher.resilient_request",
                    new_callable=AsyncMock, side_effect=Exception("down")):
            published = await publisher.publish_cycle_insights("Biz", "desc", results)
            assert published == []

    @pytest.mark.asyncio
    async def test_empty_results(self, publisher):
        published = await publisher.publish_cycle_insights("Biz", "desc", [])
        assert published == []

    @pytest.mark.asyncio
    async def test_publishes_multiple_knowledge_areas(self, publisher):
        results = [
            {"area": "strategy", "status": "completed", "result": "Strategic analysis"},
            {"area": "market_research", "status": "completed", "result": "Market trends"},
            {"area": "finance", "status": "completed", "result": "Financial outlook"},
        ]

        with patch("arclane.integrations.nexus_publisher.resilient_request",
                    new_callable=AsyncMock, return_value={"id": "k-1"}):
            published = await publisher.publish_cycle_insights("Biz", "desc", results)
            assert len(published) == 3

    @pytest.mark.asyncio
    async def test_truncates_long_analysis(self, publisher):
        results = [
            {"area": "strategy", "status": "completed", "result": "x" * 5000},
        ]

        mock_resilient = AsyncMock(return_value={"id": "k-1"})
        with patch("arclane.integrations.nexus_publisher.resilient_request", mock_resilient):
            published = await publisher.publish_cycle_insights("Biz", "desc", results)
            assert len(published) == 1
            # Verify the payload was truncated — check the json_body kwarg
            call_kwargs = mock_resilient.call_args
            payload = call_kwargs[1].get("json_body") or call_kwargs.kwargs.get("json_body")
            assert len(payload["content"]) < 5000

    @pytest.mark.asyncio
    async def test_skips_empty_analysis(self, publisher):
        results = [{"area": "strategy", "status": "completed", "result": ""}]
        published = await publisher.publish_cycle_insights("Biz", "desc", results)
        assert published == []


class TestGetRelevantKnowledge:
    @pytest.fixture
    def publisher(self):
        return NexusPublisher(base_url="http://nexus-test:8008")

    @pytest.mark.asyncio
    async def test_returns_knowledge_items(self, publisher):
        data = {
            "items": [
                {"content": "AI market growing 30% YoY", "source": "trendscope:signal", "confidence": 0.8},
                {"content": "DevOps tools demand rising", "source": "kh_graph:cluster", "confidence": 0.75},
            ]
        }

        with patch("arclane.integrations.nexus_publisher.resilient_request",
                    new_callable=AsyncMock, return_value=data):
            items = await publisher.get_relevant_knowledge("AI startup")
            assert len(items) == 2
            assert items[0]["source"] == "trendscope:signal"

    @pytest.mark.asyncio
    async def test_graceful_failure(self, publisher):
        with patch("arclane.integrations.nexus_publisher.resilient_request",
                    new_callable=AsyncMock, side_effect=Exception("timeout")):
            items = await publisher.get_relevant_knowledge("anything")
            assert items == []

    @pytest.mark.asyncio
    async def test_respects_limit(self, publisher):
        data = {"items": [{"content": f"item{i}", "source": "x", "confidence": 0.5} for i in range(20)]}

        with patch("arclane.integrations.nexus_publisher.resilient_request",
                    new_callable=AsyncMock, return_value=data):
            items = await publisher.get_relevant_knowledge("test", limit=3)
            assert len(items) == 3

    @pytest.mark.asyncio
    async def test_handles_list_response(self, publisher):
        data = [
            {"content": "Direct list item", "source": "src", "confidence": 0.9},
        ]

        with patch("arclane.integrations.nexus_publisher.resilient_request",
                    new_callable=AsyncMock, return_value=data):
            items = await publisher.get_relevant_knowledge("test")
            assert len(items) == 1
            assert items[0]["content"] == "Direct list item"


class TestFormatKnowledgeContext:
    def test_empty(self):
        p = NexusPublisher(base_url="http://test:8008")
        assert p.format_knowledge_context([]) == ""

    def test_formats_items(self):
        p = NexusPublisher(base_url="http://test:8008")
        items = [
            {"content": "AI is growing", "source": "trendscope", "confidence": 0.8},
            {"content": "DevOps demand", "source": "kh_graph", "confidence": 0.7},
        ]
        result = p.format_knowledge_context(items)
        assert "AI is growing" in result
        assert "trendscope" in result
        assert "Nexus knowledge base" in result

    def test_limits_to_five(self):
        p = NexusPublisher(base_url="http://test:8008")
        items = [{"content": f"item{i}", "source": "x", "confidence": 0.5} for i in range(10)]
        result = p.format_knowledge_context(items)
        assert "item4" in result
        assert "item5" not in result


class TestOrchestratorHasNexus:
    def test_orchestrator_has_nexus_publisher(self):
        from arclane.engine.orchestrator import ArclaneOrchestrator
        orch = ArclaneOrchestrator()
        assert hasattr(orch, "_nexus_publisher")

    def test_config_has_nexus_base_url(self):
        from arclane.core.config import ArclaneSettings
        s = ArclaneSettings()
        assert hasattr(s, "nexus_base_url")
        assert s.nexus_base_url == "http://localhost:8008"
