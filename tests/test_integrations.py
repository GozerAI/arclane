"""Tests for Trendscope and KH integrations."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from arclane.integrations.trendscope_client import TrendscopeClient
from arclane.integrations.kh_publisher import KHPublisher, CONTENT_TYPE_MAP


def _make_resp(json_data, status_code=200):
    """Create a MagicMock httpx response (json() is sync in httpx)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _patch_async_client(mock_method_name, return_value=None, side_effect=None):
    """Helper: patch httpx.AsyncClient context manager with a mock method."""
    mock_client = AsyncMock()
    if side_effect:
        getattr(mock_client, mock_method_name).side_effect = side_effect
    else:
        getattr(mock_client, mock_method_name).return_value = return_value
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ── TrendscopeClient ──────────────────────────────────────────


class TestTrendscopeClient:
    @pytest.fixture
    def client(self):
        return TrendscopeClient(base_url="http://ts-test:8002", bearer_token="test-token")

    @pytest.mark.asyncio
    async def test_get_relevant_signals_success(self, client):
        resp = _make_resp([
            {"name": "AI Agents", "score": 85, "velocity": 0.5, "category": "technology", "status": "growing"},
            {"name": "Crypto", "score": 60, "velocity": -0.2, "category": "finance", "status": "declining"},
        ])
        mock_client = _patch_async_client("get", return_value=resp)

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            signals = await client.get_relevant_signals("tech startup")
            assert len(signals) == 2
            assert signals[0]["name"] == "AI Agents"
            assert signals[0]["score"] == 85

    @pytest.mark.asyncio
    async def test_get_relevant_signals_graceful_failure(self, client):
        mock_client = _patch_async_client("get", side_effect=Exception("connection refused"))

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            signals = await client.get_relevant_signals("anything")
            assert signals == []

    @pytest.mark.asyncio
    async def test_get_relevant_signals_non_list_response(self, client):
        resp = _make_resp({"error": "bad request"})
        mock_client = _patch_async_client("get", return_value=resp)

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            signals = await client.get_relevant_signals("test")
            assert signals == []

    @pytest.mark.asyncio
    async def test_get_relevant_signals_respects_limit(self, client):
        resp = _make_resp([{"name": f"T{i}", "score": i * 10} for i in range(20)])
        mock_client = _patch_async_client("get", return_value=resp)

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            signals = await client.get_relevant_signals("test", limit=3)
            assert len(signals) == 3

    @pytest.mark.asyncio
    async def test_get_relevant_signals_sends_auth_header(self, client):
        resp = _make_resp([])
        mock_client = _patch_async_client("get", return_value=resp)

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            await client.get_relevant_signals("test")
            call_kwargs = mock_client.get.call_args[1]
            assert "Bearer test-token" in call_kwargs["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_get_relevant_signals_no_auth_without_token(self):
        client = TrendscopeClient(base_url="http://ts-test:8002")
        resp = _make_resp([])
        mock_client = _patch_async_client("get", return_value=resp)

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            await client.get_relevant_signals("test")
            call_kwargs = mock_client.get.call_args[1]
            assert "Authorization" not in call_kwargs["headers"]

    @pytest.mark.asyncio
    async def test_get_strong_buy_signals_success(self, client):
        resp = _make_resp({"trends": [{"name": "AI", "signal": "STRONG_BUY"}], "total": 1})
        mock_client = _patch_async_client("get", return_value=resp)

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            signals = await client.get_strong_buy_signals()
            assert len(signals) == 1
            assert signals[0]["signal"] == "STRONG_BUY"

    @pytest.mark.asyncio
    async def test_get_strong_buy_signals_graceful_failure(self, client):
        mock_client = _patch_async_client("get", side_effect=Exception("timeout"))

        with patch("arclane.integrations.trendscope_client.httpx.AsyncClient", return_value=mock_client):
            signals = await client.get_strong_buy_signals()
            assert signals == []

    def test_format_signal_context_empty(self):
        client = TrendscopeClient(base_url="http://test:8002")
        assert client.format_signal_context([]) == ""

    def test_format_signal_context_formats_trends(self):
        client = TrendscopeClient(base_url="http://test:8002")
        signals = [
            {"name": "AI", "score": 90, "velocity": 0.5, "category": "tech"},
            {"name": "Crypto", "score": 40, "velocity": -0.3, "category": "finance"},
            {"name": "Stable", "score": 50, "velocity": 0, "category": "other"},
        ]
        result = client.format_signal_context(signals)
        assert "AI" in result
        assert "rising" in result
        assert "falling" in result
        assert "stable" in result

    def test_format_signal_context_limits_to_five(self):
        client = TrendscopeClient(base_url="http://test:8002")
        signals = [{"name": f"T{i}", "score": i, "velocity": 0.1, "category": "x"} for i in range(10)]
        result = client.format_signal_context(signals)
        assert "T0" in result
        assert "T4" in result
        assert "T5" not in result

    def test_default_base_url_from_settings(self):
        client = TrendscopeClient()
        from arclane.core.config import settings
        assert client._base_url == settings.trendscope_base_url


# ── KHPublisher ───────────────────────────────────────────────


class TestKHPublisher:
    @pytest.fixture
    def publisher(self):
        return KHPublisher(base_url="http://kh-test:8011")

    @pytest.mark.asyncio
    async def test_publish_content_success(self, publisher):
        resp = _make_resp({"id": "art-123", "status": "created"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            result = await publisher.publish_content(
                business_name="TestBiz",
                content_type="blog",
                title="My Post",
                body="Content here",
            )
            assert result["id"] == "art-123"

    @pytest.mark.asyncio
    async def test_publish_content_graceful_failure(self, publisher):
        mock_client = _patch_async_client("post", side_effect=Exception("connection refused"))

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            result = await publisher.publish_content(
                business_name="TestBiz",
                content_type="blog",
                title="Post",
                body="Body",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_publish_content_maps_blog_to_guide(self, publisher):
        resp = _make_resp({"id": "art-1"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            await publisher.publish_content(
                business_name="Biz",
                content_type="blog",
                title="T",
                body="B",
            )
            call_json = mock_client.post.call_args[1]["json"]
            assert call_json["artifact_type"] == "guide"

    @pytest.mark.asyncio
    async def test_publish_content_maps_social_to_snippet(self, publisher):
        resp = _make_resp({"id": "art-1"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            await publisher.publish_content(
                business_name="Biz",
                content_type="social",
                title="T",
                body="B",
            )
            call_json = mock_client.post.call_args[1]["json"]
            assert call_json["artifact_type"] == "snippet"

    @pytest.mark.asyncio
    async def test_publish_content_caps_body_size(self, publisher):
        resp = _make_resp({"id": "art-1"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            await publisher.publish_content(
                business_name="Biz",
                content_type="report",
                title="Big Report",
                body="x" * 10000,
            )
            call_json = mock_client.post.call_args[1]["json"]
            assert len(call_json["content"]) == 5000

    @pytest.mark.asyncio
    async def test_publish_content_uses_fallback_title(self, publisher):
        resp = _make_resp({"id": "art-1"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            await publisher.publish_content(
                business_name="Biz",
                content_type="social",
                title="",
                body="content",
            )
            call_json = mock_client.post.call_args[1]["json"]
            assert call_json["title"] == "Biz - social"

    @pytest.mark.asyncio
    async def test_publish_content_unknown_type_defaults_to_snippet(self, publisher):
        resp = _make_resp({"id": "art-1"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            await publisher.publish_content(
                business_name="Biz",
                content_type="unknown_type",
                title="T",
                body="B",
            )
            call_json = mock_client.post.call_args[1]["json"]
            assert call_json["artifact_type"] == "snippet"

    @pytest.mark.asyncio
    async def test_publish_content_includes_metadata(self, publisher):
        resp = _make_resp({"id": "art-1"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            await publisher.publish_content(
                business_name="Biz",
                content_type="blog",
                title="T",
                body="B",
                category="marketing",
            )
            call_json = mock_client.post.call_args[1]["json"]
            assert call_json["source"] == "arclane:Biz"
            assert call_json["primary_category"] == "marketing"
            assert call_json["metadata"]["origin"] == "arclane"
            assert call_json["metadata"]["business"] == "Biz"

    @pytest.mark.asyncio
    async def test_publish_cycle_results_filters_non_content(self, publisher):
        results = [
            {"area": "strategy", "status": "completed", "result": "analysis done"},
            {"area": "content", "status": "completed", "content_type": "blog", "content_body": "Post!", "content_title": "My Blog"},
        ]

        resp = _make_resp({"id": "art-1"})
        mock_client = _patch_async_client("post", return_value=resp)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            published = await publisher.publish_cycle_results("TestBiz", results)
            assert len(published) == 1
            assert published[0]["content_type"] == "blog"

    @pytest.mark.asyncio
    async def test_publish_cycle_results_empty(self, publisher):
        published = await publisher.publish_cycle_results("TestBiz", [])
        assert published == []

    @pytest.mark.asyncio
    async def test_publish_cycle_results_partial_failure(self, publisher):
        results = [
            {"content_type": "blog", "content_body": "A", "content_title": "B"},
            {"content_type": "social", "content_body": "C", "content_title": "D"},
        ]

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("network error")
            return _make_resp({"id": "art-2"})

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("arclane.integrations.kh_publisher.httpx.AsyncClient", return_value=mock_client):
            published = await publisher.publish_cycle_results("TestBiz", results)
            assert len(published) == 1

    def test_default_base_url_from_settings(self):
        publisher = KHPublisher()
        from arclane.core.config import settings
        assert publisher._base_url == settings.kh_base_url


def test_content_type_map_coverage():
    """All expected content types are mapped."""
    for ct in ["blog", "social", "newsletter", "changelog", "report"]:
        assert ct in CONTENT_TYPE_MAP


# ── Orchestrator integration ──────────────────────────────────


class TestOrchestratorIntegration:
    def test_orchestrator_has_kh_publisher(self):
        from arclane.engine.orchestrator import ArclaneOrchestrator
        orch = ArclaneOrchestrator()
        assert hasattr(orch, "_kh_publisher")
        assert isinstance(orch._kh_publisher, KHPublisher)


class TestConfigHasKHBaseUrl:
    def test_kh_base_url_exists(self):
        from arclane.core.config import ArclaneSettings
        s = ArclaneSettings()
        assert hasattr(s, "kh_base_url")
        assert s.kh_base_url == "http://localhost:8011"
