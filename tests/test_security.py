"""Security-focused tests."""

import hashlib
import hmac as hmac_mod
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    from arclane.api.routes import cycles as cycles_module
    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    app.dependency_overrides.clear()
    await engine.dispose()


# --- Reserved slug protection ---


async def test_reserved_slug_admin(client):
    resp = await client.post("/api/businesses", json={
        "name": "Admin",
        "description": "Trying to claim admin subdomain",
        "owner_email": "attacker@example.com",
    })
    assert resp.status_code == 400
    assert "reserved" in resp.json()["detail"].lower()


async def test_reserved_slug_api(client):
    resp = await client.post("/api/businesses", json={
        "name": "API",
        "description": "Trying to claim api subdomain",
        "owner_email": "attacker@example.com",
    })
    assert resp.status_code == 400


async def test_reserved_slug_mail(client):
    resp = await client.post("/api/businesses", json={
        "name": "mail",
        "description": "Trying to claim mail subdomain",
        "owner_email": "attacker@example.com",
    })
    assert resp.status_code == 400


# --- Input validation ---


async def test_business_name_too_long(client):
    resp = await client.post("/api/businesses", json={
        "name": "x" * 300,
        "description": "Normal description",
        "owner_email": "test@example.com",
    })
    assert resp.status_code == 422


async def test_business_description_too_long(client):
    resp = await client.post("/api/businesses", json={
        "name": "Normal Name",
        "description": "x" * 20000,
        "owner_email": "test@example.com",
    })
    assert resp.status_code == 422


async def test_invalid_template_rejected(client):
    resp = await client.post("/api/businesses", json={
        "name": "Template Test",
        "description": "Testing invalid template",
        "owner_email": "test@example.com",
        "template": "../../etc/passwd",
    })
    assert resp.status_code == 422


async def test_valid_template_accepted(client):
    resp = await client.post("/api/businesses", json={
        "name": "Template OK",
        "description": "Testing valid template",
        "owner_email": "test@example.com",
        "template": "content-site",
    })
    assert resp.status_code == 201


async def test_task_description_too_long(client):
    await client.post("/api/businesses", json={
        "name": "Task Len Test",
        "description": "Testing task length",
        "owner_email": "test@example.com",
    })
    resp = await client.post("/api/businesses/task-len-test/cycles", json={
        "task_description": "x" * 6000,
    })
    assert resp.status_code == 422


# --- Business list requires email ---


async def test_list_businesses_requires_email(client):
    resp = await client.get("/api/businesses")
    assert resp.status_code == 400


async def test_list_businesses_with_email(client):
    await client.post("/api/businesses", json={
        "name": "Email Filter",
        "description": "Testing email filter",
        "owner_email": "filter@example.com",
    })
    resp = await client.get("/api/businesses?owner_email=filter@example.com")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_list_businesses_wrong_email_empty(client):
    await client.post("/api/businesses", json={
        "name": "No Leak",
        "description": "Should not appear",
        "owner_email": "real@example.com",
    })
    resp = await client.get("/api/businesses?owner_email=other@example.com")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


# --- Webhook security ---


async def test_webhook_rejects_empty_token(client):
    await client.post("/api/businesses", json={
        "name": "Webhook Sec",
        "description": "Testing webhook security",
        "owner_email": "test@example.com",
    })
    resp = await client.post(
        "/api/businesses/webhook-sec/billing/webhook",
        json={
            "event": "subscription.created",
            "customer_email": "test@example.com",
            "plan": "pro",
        },
        headers={"X-Service-Token": ""},
    )
    assert resp.status_code == 403


async def test_webhook_rejects_wrong_token(client):
    from arclane.core.config import settings
    original = settings.zuul_service_token
    settings.zuul_service_token = "real-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Webhook Wrong",
            "description": "Testing wrong token",
            "owner_email": "test@example.com",
        })
        resp = await client.post(
            "/api/businesses/webhook-wrong/billing/webhook",
            json={
                "event": "subscription.created",
                "customer_email": "test@example.com",
                "plan": "pro",
            },
            headers={"X-Service-Token": "wrong-secret"},
        )
        assert resp.status_code == 403
    finally:
        settings.zuul_service_token = original


async def test_webhook_rejects_invalid_event(client):
    from arclane.core.config import settings
    original = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Webhook Event",
            "description": "Testing invalid event",
            "owner_email": "test@example.com",
        })
        resp = await client.post(
            "/api/businesses/webhook-event/billing/webhook",
            json={
                "event": "evil.event",
                "customer_email": "test@example.com",
                "plan": "pro",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        assert resp.status_code == 400
    finally:
        settings.zuul_service_token = original


# --- Content filter validation ---


async def test_content_filter_rejects_invalid_type(client):
    await client.post("/api/businesses", json={
        "name": "Content Filter",
        "description": "Testing content filter",
        "owner_email": "test@example.com",
    })
    resp = await client.get("/api/businesses/content-filter/content?content_type=invalid")
    assert resp.status_code == 400


async def test_content_filter_rejects_invalid_status(client):
    await client.post("/api/businesses", json={
        "name": "Status Filter",
        "description": "Testing status filter",
        "owner_email": "test@example.com",
    })
    resp = await client.get("/api/businesses/status-filter/content?status=invalid")
    assert resp.status_code == 400


# --- Health endpoint doesn't leak info ---


async def test_health_no_leak(client):
    resp = await client.get("/health")
    data = resp.json()
    assert data == {"status": "ok"}
    assert "domain" not in data
    assert "service" not in data


# --- Security headers ---


async def test_security_headers_present(client):
    resp = await client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


async def test_csp_header_present(client):
    resp = await client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


# --- HMAC webhook signature ---


async def test_webhook_hmac_valid(client):
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    orig_secret = settings.webhook_signing_secret
    settings.zuul_service_token = "test-token"
    settings.webhook_signing_secret = "test-hmac-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "HMAC Valid",
            "description": "Testing HMAC",
            "owner_email": "hmac@example.com",
        })
        body = json.dumps({
            "event": "subscription.created",
            "customer_email": "hmac@example.com",
            "plan": "pro",
            "business_slug": "hmac-valid",
        }).encode()
        sig = hmac_mod.new(b"test-hmac-secret", body, hashlib.sha256).hexdigest()
        resp = await client.post(
            "/api/businesses/hmac-valid/billing/webhook",
            content=body,
            headers={
                "X-Service-Token": "test-token",
                "X-Webhook-Signature": sig,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
    finally:
        settings.zuul_service_token = orig_token
        settings.webhook_signing_secret = orig_secret


async def test_webhook_hmac_invalid(client):
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    orig_secret = settings.webhook_signing_secret
    settings.zuul_service_token = "test-token"
    settings.webhook_signing_secret = "test-hmac-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "HMAC Invalid",
            "description": "Testing bad HMAC",
            "owner_email": "hmac2@example.com",
        })
        resp = await client.post(
            "/api/businesses/hmac-invalid/billing/webhook",
            json={
                "event": "subscription.created",
                "customer_email": "hmac2@example.com",
                "plan": "pro",
            },
            headers={
                "X-Service-Token": "test-token",
                "X-Webhook-Signature": "bad-signature",
            },
        )
        assert resp.status_code == 403
    finally:
        settings.zuul_service_token = orig_token
        settings.webhook_signing_secret = orig_secret


async def test_webhook_no_hmac_when_not_configured(client):
    """When webhook_signing_secret is empty, HMAC check is skipped."""
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    orig_secret = settings.webhook_signing_secret
    settings.zuul_service_token = "test-token"
    settings.webhook_signing_secret = ""
    try:
        await client.post("/api/businesses", json={
            "name": "No HMAC",
            "description": "Testing no HMAC",
            "owner_email": "nohmac@example.com",
        })
        resp = await client.post(
            "/api/businesses/no-hmac/billing/webhook",
            json={
                "event": "subscription.created",
                "customer_email": "nohmac@example.com",
                "plan": "pro",
                "business_slug": "no-hmac",
            },
            headers={"X-Service-Token": "test-token"},
        )
        assert resp.status_code == 200
    finally:
        settings.zuul_service_token = orig_token
        settings.webhook_signing_secret = orig_secret
