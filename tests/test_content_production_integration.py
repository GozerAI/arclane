"""Tests for Arclane -> Content Production integration."""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

from arclane.integrations.content_production_client import (
    ContentProductionClient,
    MARKETPLACE_PLATFORMS,
)


# ── ContentProductionClient ──────────────────────────────────


class TestContentProductionClientHealthCheck:
    def test_health_check_success(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")
        with patch.object(client, "_request", return_value={"status": "ok"}):
            assert client.health_check() is True

    def test_health_check_failure(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")
        with patch.object(client, "_request", return_value=None):
            assert client.health_check() is False


class TestContentProductionClientDistribute:
    def test_distribute_sends_correct_payload(self):
        client = ContentProductionClient(
            base_url="http://cp-test:8013",
            service_token="test-svc-token",
        )
        expected_result = {"job_id": "job-123", "status": "queued"}

        with patch.object(client, "_request", return_value=expected_result) as mock_req:
            result = client.distribute_content(
                title="My Ebook",
                description="A great ebook about AI",
                content_body="Full content here...",
                platforms=["gumroad", "etsy"],
                marketplace_credentials={
                    "gumroad_api_token": "gum-key-123",
                    "etsy_api_key": "etsy-key-456",
                },
                revenue_webhook_url="http://localhost:8012/api/businesses/mybiz/webhooks/revenue",
                tags=["ai", "ebook"],
                price_usd=9.99,
            )

            assert result == expected_result
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][0] == "POST"
            assert call_args[0][1] == "/v1/distribute"

            payload = call_args[0][2]
            assert payload["title"] == "My Ebook"
            assert payload["description"] == "A great ebook about AI"
            assert payload["content_body"] == "Full content here..."
            assert payload["platforms"] == ["gumroad", "etsy"]
            assert payload["marketplace_credentials"]["gumroad_api_token"] == "gum-key-123"
            assert payload["revenue_webhook_url"] == "http://localhost:8012/api/businesses/mybiz/webhooks/revenue"
            assert payload["tags"] == ["ai", "ebook"]
            assert payload["price_usd"] == 9.99

    def test_distribute_includes_revenue_webhook_url(self):
        """Revenue webhook URL is included in the distribution request."""
        client = ContentProductionClient(base_url="http://cp-test:8013")
        webhook_url = "http://localhost:8012/api/businesses/test-biz/webhooks/revenue"

        with patch.object(client, "_request", return_value={"job_id": "j1"}) as mock_req:
            client.distribute_content(
                title="Title",
                description="Desc",
                content_body="Body",
                platforms=["gumroad"],
                marketplace_credentials={"gumroad_api_token": "tok"},
                revenue_webhook_url=webhook_url,
            )

            payload = mock_req.call_args[0][2]
            assert payload["revenue_webhook_url"] == webhook_url

    def test_distribute_without_optional_fields(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")

        with patch.object(client, "_request", return_value={"job_id": "j1"}) as mock_req:
            client.distribute_content(
                title="Title",
                description="Desc",
                content_body="Body",
                platforms=["shopify"],
                marketplace_credentials={"shopify_api_token": "tok"},
            )

            payload = mock_req.call_args[0][2]
            assert payload["tags"] == []
            assert "price_usd" not in payload
            assert "revenue_webhook_url" not in payload


class TestContentProductionClientGracefulFailure:
    def test_connection_error_returns_none(self):
        client = ContentProductionClient(base_url="http://unreachable:9999")

        # Patch urllib.request.urlopen to raise
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = client.distribute_content(
                title="T",
                description="D",
                content_body="B",
                platforms=["gumroad"],
                marketplace_credentials={},
            )
            assert result is None

    def test_get_distribution_status_failure(self):
        client = ContentProductionClient(base_url="http://unreachable:9999")

        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = client.get_distribution_status("job-404")
            assert result is None


class TestContentProductionClientAuth:
    def test_headers_include_service_token(self):
        client = ContentProductionClient(
            base_url="http://cp-test:8013",
            service_token="my-secret-token",
        )
        headers = client._headers()
        assert headers["X-Service-Token"] == "my-secret-token"

    def test_headers_no_token_when_empty(self):
        client = ContentProductionClient(base_url="http://cp-test:8013", service_token="")
        headers = client._headers()
        assert "X-Service-Token" not in headers


class TestContentProductionClientGetStatus:
    def test_get_distribution_status_success(self):
        client = ContentProductionClient(base_url="http://cp-test:8013")
        status_resp = {"job_id": "job-123", "status": "completed", "results": {}}

        with patch.object(client, "_request", return_value=status_resp) as mock_req:
            result = client.get_distribution_status("job-123")
            assert result == status_resp
            mock_req.assert_called_once_with("GET", "/v1/distribute/job-123/status")


class TestMarketplacePlatformConstants:
    def test_marketplace_platforms_defined(self):
        assert "gumroad" in MARKETPLACE_PLATFORMS
        assert "etsy" in MARKETPLACE_PLATFORMS
        assert "shopify" in MARKETPLACE_PLATFORMS
        assert "amazon-kdp" in MARKETPLACE_PLATFORMS
        assert "twitter" not in MARKETPLACE_PLATFORMS


# ── Distribution Service Marketplace Delegation ──────────────


class TestDistributionServiceMarketplace:
    @pytest.fixture
    async def db_session(self):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from arclane.models.tables import Base

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            yield session

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    def _make_business(self, slug="test-biz", marketplace_creds=None):
        from arclane.models.tables import Business
        agent_config = {}
        if marketplace_creds:
            agent_config["marketplace_credentials"] = marketplace_creds
        return Business(
            id=1,
            slug=slug,
            name="Test Business",
            description="A test business",
            owner_email="test@example.com",
            agent_config=agent_config,
        )

    def _make_content(self, business_id=1):
        from arclane.models.tables import Content
        return Content(
            id=10,
            business_id=business_id,
            content_type="blog",
            title="My Ebook",
            body="Full ebook content",
            metadata_json={"description": "An ebook", "tags": ["ai"], "price_usd": 4.99},
        )

    @pytest.mark.asyncio
    async def test_marketplace_channel_delegates_to_cp(self, db_session):
        from arclane.models.tables import DistributionChannel
        from arclane.services import distribution_service

        business = self._make_business(
            marketplace_creds={"gumroad_api_token": "gum-key"},
        )
        db_session.add(business)
        await db_session.flush()

        content = self._make_content(business_id=business.id)
        db_session.add(content)
        await db_session.flush()

        channel = DistributionChannel(
            business_id=business.id,
            platform="gumroad",
            status="active",
        )
        db_session.add(channel)
        await db_session.flush()

        mock_client = MagicMock()
        mock_client.distribute_content.return_value = {"job_id": "j-1", "status": "queued"}

        with patch.object(distribution_service, "_get_cp_client", return_value=mock_client):
            from arclane.services.distribution_service import distribute_content
            result = await distribute_content(business, content, db_session)

        assert result["channels"]["gumroad"]["status"] == "distributed"
        mock_client.distribute_content.assert_called_once()

        # Verify revenue_webhook_url was included
        call_kwargs = mock_client.distribute_content.call_args[1]
        assert "revenue_webhook_url" in call_kwargs
        assert "test-biz" in call_kwargs["revenue_webhook_url"]
        assert "/webhooks/revenue" in call_kwargs["revenue_webhook_url"]

    @pytest.mark.asyncio
    async def test_regular_channel_not_delegated_to_cp(self, db_session):
        from arclane.models.tables import DistributionChannel
        from arclane.services import distribution_service

        business = self._make_business()
        db_session.add(business)
        await db_session.flush()

        content = self._make_content(business_id=business.id)
        db_session.add(content)
        await db_session.flush()

        channel = DistributionChannel(
            business_id=business.id,
            platform="twitter",
            status="active",
        )
        db_session.add(channel)
        await db_session.flush()

        mock_client = MagicMock()

        with patch.object(distribution_service, "_get_cp_client", return_value=mock_client):
            from arclane.services.distribution_service import distribute_content
            result = await distribute_content(business, content, db_session)

        # Twitter is a regular channel, should NOT go to CP
        mock_client.distribute_content.assert_not_called()
        assert result["channels"]["twitter"]["status"] == "distributed"

    @pytest.mark.asyncio
    async def test_cp_failure_marks_marketplace_as_failed(self, db_session):
        from arclane.models.tables import DistributionChannel
        from arclane.services import distribution_service

        business = self._make_business(
            marketplace_creds={"etsy_api_key": "etsy-key"},
        )
        db_session.add(business)
        await db_session.flush()

        content = self._make_content(business_id=business.id)
        db_session.add(content)
        await db_session.flush()

        channel = DistributionChannel(
            business_id=business.id,
            platform="etsy",
            status="active",
        )
        db_session.add(channel)
        await db_session.flush()

        mock_client = MagicMock()
        mock_client.distribute_content.return_value = None  # CP unavailable

        with patch.object(distribution_service, "_get_cp_client", return_value=mock_client):
            from arclane.services.distribution_service import distribute_content
            result = await distribute_content(business, content, db_session)

        assert result["channels"]["etsy"]["status"] == "failed"
        assert "unavailable" in result["channels"]["etsy"]["error"].lower()


# ── Marketplace Credential Management ────────────────────────


class TestMarketplaceCredentialRoutes:
    """Test credential storage/retrieval logic (unit-level, no HTTP)."""

    def test_get_configured_platforms_from_creds(self):
        from arclane.api.routes.distribution import _get_configured_platforms

        creds = {
            "gumroad_api_token": "tok-123",
            "shopify_store_url": "https://mystore.myshopify.com",
            "shopify_api_token": "shpat-123",
        }
        result = _get_configured_platforms(creds)
        assert "gumroad" in result
        assert "shopify" in result
        assert "etsy" not in result

    def test_get_configured_platforms_empty(self):
        from arclane.api.routes.distribution import _get_configured_platforms

        assert _get_configured_platforms({}) == []

    def test_get_configured_platforms_ignores_empty_values(self):
        from arclane.api.routes.distribution import _get_configured_platforms

        creds = {"gumroad_api_token": "", "etsy_api_key": None}
        assert _get_configured_platforms(creds) == []

    def test_credential_platform_map_covers_all_available(self):
        from arclane.api.routes.distribution import _CREDENTIAL_PLATFORM_MAP, AVAILABLE_MARKETPLACES

        mapped_platforms = set(_CREDENTIAL_PLATFORM_MAP.values())
        for mp in AVAILABLE_MARKETPLACES:
            assert mp in mapped_platforms, f"{mp} not covered by _CREDENTIAL_PLATFORM_MAP"


class TestMarketplaceCredentialStorage:
    @pytest.fixture
    async def db_session(self):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from arclane.models.tables import Base

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            yield session

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_store_and_retrieve_credentials(self, db_session):
        """Credentials stored in agent_config can be read back."""
        from arclane.models.tables import Business

        business = Business(
            slug="cred-test",
            name="Cred Test",
            description="Testing creds",
            owner_email="test@example.com",
            agent_config={},
        )
        db_session.add(business)
        await db_session.flush()

        # Store credentials
        agent_config = dict(business.agent_config or {})
        agent_config["marketplace_credentials"] = {
            "gumroad_api_token": "gum-tok",
            "shopify_api_token": "shp-tok",
            "shopify_store_url": "https://my.myshopify.com",
        }
        business.agent_config = agent_config
        await db_session.flush()

        # Retrieve
        creds = business.agent_config["marketplace_credentials"]
        assert creds["gumroad_api_token"] == "gum-tok"
        assert creds["shopify_api_token"] == "shp-tok"

    @pytest.mark.asyncio
    async def test_delete_credential_removes_platform_keys(self, db_session):
        """Deleting a platform removes all its credential keys."""
        from arclane.models.tables import Business
        from arclane.api.routes.distribution import _CREDENTIAL_PLATFORM_MAP

        business = Business(
            slug="del-test",
            name="Del Test",
            description="Testing deletion",
            owner_email="test@example.com",
            agent_config={
                "marketplace_credentials": {
                    "gumroad_api_token": "gum-tok",
                    "shopify_api_token": "shp-tok",
                    "shopify_store_url": "https://my.myshopify.com",
                }
            },
        )
        db_session.add(business)
        await db_session.flush()

        # Simulate deletion of shopify credentials
        creds = dict(business.agent_config["marketplace_credentials"])
        keys_to_remove = [k for k, v in _CREDENTIAL_PLATFORM_MAP.items() if v == "shopify"]
        for key in keys_to_remove:
            creds.pop(key, None)

        agent_config = dict(business.agent_config)
        agent_config["marketplace_credentials"] = creds
        business.agent_config = agent_config
        await db_session.flush()

        remaining = business.agent_config["marketplace_credentials"]
        assert "shopify_api_token" not in remaining
        assert "shopify_store_url" not in remaining
        assert "gumroad_api_token" in remaining


# ── Revenue Webhook URL in Distribution ──────────────────────


class TestRevenueWebhookUrlInDistribution:
    """Verify the revenue webhook URL is constructed and sent correctly."""

    def test_webhook_url_format(self):
        """The webhook URL follows the expected pattern."""
        slug = "my-business"
        url = f"http://localhost:8012/api/businesses/{slug}/webhooks/revenue"
        assert url == "http://localhost:8012/api/businesses/my-business/webhooks/revenue"

    @pytest.mark.asyncio
    async def test_distribute_via_cp_sends_webhook_url(self):
        """_distribute_via_content_production includes revenue_webhook_url."""
        from arclane.models.tables import Business, Content, DistributionChannel
        from arclane.services.distribution_service import _distribute_via_content_production

        business = Business(
            id=1,
            slug="webhook-test",
            name="WH Test",
            description="Test",
            owner_email="test@example.com",
            agent_config={"marketplace_credentials": {"gumroad_api_token": "tok"}},
        )
        content = Content(
            id=5,
            business_id=1,
            content_type="blog",
            title="Title",
            body="Body",
        )
        channels = [
            MagicMock(spec=DistributionChannel, platform="gumroad"),
        ]

        mock_client = MagicMock()
        mock_client.distribute_content.return_value = {"job_id": "j1"}

        with patch("arclane.services.distribution_service._get_cp_client", return_value=mock_client):
            await _distribute_via_content_production(business, content, channels)

        call_kwargs = mock_client.distribute_content.call_args[1]
        assert call_kwargs["revenue_webhook_url"] == "http://localhost:8012/api/businesses/webhook-test/webhooks/revenue"
