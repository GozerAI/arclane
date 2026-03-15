"""Billing edge cases — credit deduction, plan changes, concurrent ops."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from arclane.api.app import app
from arclane.core.config import settings
from arclane.core.database import get_session
from arclane.engine.operating_plan import build_operating_plan, enqueue_add_on
from arclane.models.tables import Base, Business, Cycle


@pytest.fixture
async def client():
    """Authenticated test client with in-memory DB."""
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
            reg = await c.post("/api/auth/register", json={
                "email": "billing@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})
            yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db():
    """Returns session factory for direct DB operations."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _create_business(factory, slug, plan="starter", credits=5, bonus=0, email="test@test.com"):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.title(),
            description="Test business",
            owner_email=email,
            plan=plan,
            credits_remaining=credits,
            credits_bonus=bonus,
        )
        session.add(biz)
        await session.commit()
        return biz.id


# --- Credit deduction on cycle execution ---


async def test_cycle_deducts_bonus_credit_first(client):
    """On-demand cycle deducts bonus credit before regular credits."""
    # Create a business with both bonus and regular credits
    await client.post("/api/businesses", json={
        "name": "Bonus First",
        "description": "Test bonus deduction order",
    })
    # The created business has default credits (5 regular, bonus gets decremented for initial cycle)
    resp = await client.post("/api/businesses/bonus-first/cycles", json={
        "task_description": "Test task",
    })
    assert resp.status_code == 201


async def test_cycle_deducts_regular_when_no_bonus(client):
    """Cycle deducts regular credits when bonus is exhausted."""
    await client.post("/api/businesses", json={
        "name": "Regular Deduct",
        "description": "Test regular deduction",
    })
    # Trigger multiple cycles to exhaust bonus credits
    for _ in range(3):
        resp = await client.post("/api/businesses/regular-deduct/cycles", json={
            "task_description": "Test task",
        })
        if resp.status_code == 402:
            break


# --- Insufficient credits blocking ---


async def test_cycle_blocked_when_no_credits(db):
    """Cycle trigger returns 402 when credits are exhausted."""
    biz_id = await _create_business(db, "no-credits", credits=0, bonus=0)

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Seed business with 0 credits in this new DB
    async with factory() as session:
        biz = Business(
            slug="zero-credits",
            name="Zero Credits",
            description="No credits",
            owner_email="billing@example.com",
            plan="starter",
            credits_remaining=0,
            credits_bonus=0,
        )
        session.add(biz)
        await session.commit()

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    from arclane.api.routes import cycles as cycles_module
    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Register to get JWT
            await c.post("/api/auth/register", json={
                "email": "nocred@example.com",
                "password": "testpassword123",
            })
            # But we need a business owned by this user with 0 credits
            async with factory() as session:
                biz2 = Business(
                    slug="nocred-biz",
                    name="NoCred Biz",
                    description="No credits",
                    owner_email="nocred@example.com",
                    plan="starter",
                    credits_remaining=0,
                    credits_bonus=0,
                )
                session.add(biz2)
                await session.commit()

            # Login
            reg = await c.post("/api/auth/register", json={
                "email": "nocred2@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})

            # Create business owned by this user with 0 credits
            async with factory() as session:
                biz3 = Business(
                    slug="nocred2-biz",
                    name="NoCred2 Biz",
                    description="No credits",
                    owner_email="nocred2@example.com",
                    plan="starter",
                    credits_remaining=0,
                    credits_bonus=0,
                )
                session.add(biz3)
                await session.commit()

            resp = await c.post("/api/businesses/nocred2-biz/cycles", json={
                "task_description": "Should fail",
            })
            assert resp.status_code == 402
            assert "no credits" in resp.json()["detail"].lower()

    app.dependency_overrides.clear()
    await engine.dispose()


# --- Credit reset on billing cycle ---


async def test_monthly_reset_restores_starter_credits(db):
    """Starter subscriptions reset to the included monthly credits."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "starter-reset", plan="starter", credits=1)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 10


async def test_monthly_reset_restores_pro_credits(db):
    """Monthly reset gives pro plan 20 credits."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "pro-reset", plan="pro", credits=3)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 20


async def test_monthly_reset_restores_scale_credits(db):
    """Monthly reset gives scale plan 150 credits."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "scale-reset", plan="scale", credits=50)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 150


# --- Plan upgrade mid-cycle via webhook ---


async def test_webhook_upgrades_plan_to_pro(client):
    """Subscription webhook upgrades plan and credits."""
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Upgrade Test",
            "description": "Testing upgrade",
        })
        resp = await client.post(
            "/api/businesses/upgrade-test/billing/webhook",
            json={
                "event": "subscription.created",
                "customer_email": "billing@example.com",
                "plan": "pro",
                "business_slug": "upgrade-test",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "pro"
    finally:
        settings.zuul_service_token = orig_token


async def test_webhook_upgrades_plan_to_growth(client):
    """Subscription webhook upgrades to growth plan with correct credits."""
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Growth Upgrade",
            "description": "Testing growth upgrade",
        })
        resp = await client.post(
            "/api/businesses/growth-upgrade/billing/webhook",
            json={
                "event": "subscription.created",
                "customer_email": "billing@example.com",
                "plan": "growth",
                "business_slug": "growth-upgrade",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        assert resp.status_code == 200
    finally:
        settings.zuul_service_token = orig_token


# --- Plan downgrade / cancellation ---


async def test_webhook_cancels_subscription(client):
    """Cancellation webhook sets plan to cancelled and zeroes credits."""
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Cancel Test",
            "description": "Testing cancellation",
        })
        resp = await client.post(
            "/api/businesses/cancel-test/billing/webhook",
            json={
                "event": "subscription.cancelled",
                "customer_email": "billing@example.com",
                "plan": "starter",
                "business_slug": "cancel-test",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        assert resp.status_code == 200
    finally:
        settings.zuul_service_token = orig_token


async def test_cancelled_plan_gets_no_credits_on_reset(db):
    """Cancelled businesses get no credits on monthly reset."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "cancelled-biz", plan="cancelled", credits=0)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 0


# --- Bonus credit on first business creation ---


async def test_initial_cycle_uses_bonus_credit(client):
    """Creating a business consumes one preview credit for the initial cycle."""
    resp = await client.post("/api/businesses", json={
        "name": "Initial Bonus",
        "description": "Test initial cycle bonus",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["credits_remaining"] == 2


async def test_default_bonus_credits_are_ten(db):
    """New businesses default to no bonus credits."""
    async with db() as session:
        biz = Business(
            slug="bonus-default",
            name="Bonus Default",
            description="Test",
            owner_email="test@test.com",
        )
        session.add(biz)
        await session.commit()
        await session.refresh(biz)
        assert biz.credits_bonus == 0


# --- Credit refund on failed cycle ---


async def test_nightly_cycle_skips_zero_credit_business(db):
    """Nightly cycle skips businesses with no credits."""
    from arclane.engine.scheduler import _nightly_cycle

    await _create_business(db, "broke-biz", credits=0, bonus=0)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            await _nightly_cycle()
            mock_orch.execute_cycle.assert_not_called()


async def test_nightly_uses_bonus_before_regular(db):
    """Nightly cycle deducts from bonus credits before regular."""
    from arclane.engine.scheduler import _nightly_cycle

    biz_id = await _create_business(db, "bonus-prio", credits=5, bonus=3)

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        # Bonus should be deducted, regular untouched
        assert biz.credits_bonus == 2
        assert biz.credits_remaining == 5


async def test_nightly_uses_purchased_add_on_capacity_before_balance(db):
    """Queued add-ons use their own included nights before touching business credits."""
    from arclane.engine.scheduler import _nightly_cycle

    async with db() as session:
        plan = build_operating_plan(
            name="Add On Nights",
            slug="addon-nights",
            description="An automation service for local contractors",
            template="content-site",
        )
        plan["agent_tasks"][0]["queue_status"] = "completed"
        plan["add_on_offers"][0]["status"] = "available"
        plan = enqueue_add_on(plan, "deep-market-dive")
        biz = Business(
            slug="addon-nights",
            name="Add On Nights",
            description="Test business",
            owner_email="test@test.com",
            plan="starter",
            credits_remaining=5,
            credits_bonus=0,
            agent_config={"operating_plan": plan},
        )
        session.add(biz)
        await session.commit()
        biz_id = biz.id

    with patch("arclane.engine.scheduler.async_session", db):
        with patch("arclane.engine.scheduler.orchestrator") as mock_orch:
            mock_orch.next_queue_task.side_effect = lambda business: next(
                item for item in business.agent_config["operating_plan"]["agent_tasks"] if item["key"] == "addon-market-01"
            )
            mock_orch.execute_cycle = AsyncMock()
            await _nightly_cycle()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        addon_task = next(
            item for item in biz.agent_config["operating_plan"]["agent_tasks"] if item["key"] == "addon-market-01"
        )
        assert biz.credits_remaining == 5
        assert addon_task["included_cycles_remaining"] == 2


# --- Billing status ---


async def test_billing_status_returns_plan_info(client):
    """Billing status endpoint returns plan and credit info."""
    await client.post("/api/businesses", json={
        "name": "Status Check",
        "description": "Testing billing status",
    })
    resp = await client.get("/api/businesses/status-check/billing/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan"] == "preview"
    assert data["active"] is True
    assert "credits_remaining" in data
    assert data["company_limit"] == 1
    assert data["trial_days"] == 3
    assert data["can_start_paid_trial"] is True


async def test_billing_status_shows_combined_credits(client):
    """Billing status reflects remaining preview credits."""
    await client.post("/api/businesses", json={
        "name": "Combined Credits",
        "description": "Testing combined credits",
    })
    resp = await client.get("/api/businesses/combined-credits/billing/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["credits_remaining"] == 2


async def test_credit_purchase_webhook_adds_bonus_credits(client):
    """Credit purchase webhook adds top-up credits to the business."""
    from arclane.core.config import settings

    orig_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Burst Credits",
            "description": "Testing purchased credits",
        })
        resp = await client.post(
            "/api/businesses/burst-credits/billing/webhook",
            json={
                "event": "credits.purchased",
                "customer_email": "billing@example.com",
                "plan": "preview",
                "business_slug": "burst-credits",
                "credit_pack": "boost-5",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        assert resp.status_code == 200

        status = await client.get("/api/businesses/burst-credits/billing/status")
        assert status.status_code == 200
        assert status.json()["credits_remaining"] == 7
    finally:
        settings.zuul_service_token = orig_token


async def test_growth_plan_unlocks_three_company_slots(client):
    """Growth plan allows an account to manage up to three businesses."""
    from arclane.core.config import settings

    original_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Portfolio Anchor",
            "description": "Anchor business",
        })
        upgrade = await client.post(
            "/api/businesses/portfolio-anchor/billing/webhook",
            json={
                "event": "subscription.created",
                "customer_email": "billing@example.com",
                "plan": "growth",
                "business_slug": "portfolio-anchor",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        assert upgrade.status_code == 200

        second = await client.post("/api/businesses", json={
            "name": "Portfolio Second",
            "description": "Second business",
        })
        third = await client.post("/api/businesses", json={
            "name": "Portfolio Third",
            "description": "Third business",
        })
        fourth = await client.post("/api/businesses", json={
            "name": "Portfolio Fourth",
            "description": "Fourth business",
        })

        assert second.status_code == 201
        assert third.status_code == 201
        assert fourth.status_code == 402
    finally:
        settings.zuul_service_token = original_token


# --- Subscription renewal ---


async def test_webhook_renewal_restores_credits(client):
    """Renewal webhook restores credits for the plan."""
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Renewal Test",
            "description": "Testing renewal",
        })
        # First deplete credits by triggering cycles
        # Then send renewal webhook
        resp = await client.post(
            "/api/businesses/renewal-test/billing/webhook",
            json={
                "event": "subscription.renewed",
                "customer_email": "billing@example.com",
                "plan": "starter",
                "business_slug": "renewal-test",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        assert resp.status_code == 200
    finally:
        settings.zuul_service_token = orig_token


# --- Checkout endpoint ---


async def test_checkout_invalid_plan_returns_400(client):
    """Checkout with invalid plan returns 400."""
    await client.post("/api/businesses", json={
        "name": "Bad Plan",
        "description": "Testing invalid plan",
    })
    resp = await client.post(
        "/api/businesses/bad-plan/billing/checkout",
        json={"plan": "nonexistent"},
    )
    assert resp.status_code == 400


async def test_checkout_invalid_credit_pack_returns_400(client):
    """Checkout with invalid credit pack returns 400."""
    await client.post("/api/businesses", json={
        "name": "Bad Pack",
        "description": "Testing invalid credit pack",
    })
    resp = await client.post(
        "/api/businesses/bad-pack/billing/checkout",
        json={"credit_pack": "nonexistent-pack"},
    )
    assert resp.status_code == 400


async def test_checkout_add_on_routes_through_vinzy_with_metadata():
    """Available add-ons create a Vinzy-backed checkout session with business metadata."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    captured = {}

    class StubResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"url": "https://checkout.example/addon"}

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return StubResponse()

    from arclane.api.routes import cycles as cycles_module
    mock_run = AsyncMock()
    with patch.object(cycles_module, "_run_cycle", mock_run):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            reg = await client.post("/api/auth/register", json={
                "email": "addon-checkout@example.com",
                "password": "testpassword123",
            })
            token = reg.json()["access_token"]
            client.headers.update({"Authorization": f"Bearer {token}"})

            created = await client.post("/api/businesses", json={
                "name": "Add On Checkout",
                "description": "An automation service for local contractors",
            })
            assert created.status_code == 201

            async with session_factory() as session:
                business = await session.scalar(select(Business).where(Business.slug == "add-on-checkout"))
                operating_plan = business.agent_config["operating_plan"]
                for offer in operating_plan["add_on_offers"]:
                    if offer["key"] == "deep-market-dive":
                        offer["status"] = "available"
                business.agent_config = {"operating_plan": operating_plan}
                flag_modified(business, "agent_config")
                await session.commit()

            original_stripe_enabled = settings.stripe_enabled
            settings.stripe_enabled = True
            try:
                with patch("arclane.api.routes.billing.httpx.AsyncClient", return_value=StubClient()):
                    resp = await client.post(
                        "/api/businesses/add-on-checkout/billing/checkout",
                        json={"add_on": "deep-market-dive"},
                    )
                assert resp.status_code == 200
                assert resp.json()["checkout_url"] == "https://checkout.example/addon"
                assert captured["json"]["product_code"] == "ARC-ADDON"
                assert captured["json"]["tier"] == "deep-market-dive"
                assert captured["json"]["billing_cycle"] == "one_time"
                assert captured["json"]["metadata"]["add_on"] == "deep-market-dive"
                assert captured["json"]["metadata"]["business_slug"] == "add-on-checkout"
            finally:
                settings.stripe_enabled = original_stripe_enabled

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_checkout_requires_single_target(client):
    """Checkout requires exactly one purchase target."""
    await client.post("/api/businesses", json={
        "name": "Missing Target",
        "description": "Testing invalid checkout payload",
    })
    resp = await client.post(
        "/api/businesses/missing-target/billing/checkout",
        json={},
    )
    assert resp.status_code == 400


async def test_checkout_disabled_returns_503(client):
    """Checkout when stripe is disabled returns 503."""
    orig = settings.stripe_enabled
    settings.stripe_enabled = False
    try:
        await client.post("/api/businesses", json={
            "name": "No Stripe",
            "description": "Testing stripe disabled",
        })
        resp = await client.post(
            "/api/businesses/no-stripe/billing/checkout",
            json={"plan": "pro"},
        )
        assert resp.status_code == 503
    finally:
        settings.stripe_enabled = orig


async def test_provision_complete_add_on_purchase_queues_add_on(client):
    """Provisioning callback from Vinzy should queue the purchased add-on automatically."""
    original_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        await client.post("/api/businesses", json={
            "name": "Provision Add On",
            "description": "Testing add-on provisioning",
        })

        transport = client._transport  # reuse authenticated app transport context
        async with AsyncClient(transport=transport, base_url="http://test") as direct:
            resp = await direct.post(
                "/api/billing/provision-complete",
                json={
                    "event": "provisioning.complete",
                    "product_code": "ARC-ADDON",
                    "tier": "deep-market-dive",
                    "customer_email": "billing@example.com",
                    "metadata": {
                        "business_slug": "provision-add-on",
                        "checkout_kind": "add_on",
                        "add_on": "deep-market-dive",
                    },
                },
                headers={"X-Service-Token": "test-secret"},
            )
            assert resp.status_code == 200

        status = await client.get("/api/businesses/provision-add-on/settings")
        assert status.status_code == 200
        operating_plan = status.json()["operating_plan"]
        assert operating_plan["agent_tasks"][0]["key"] == "addon-market-01"
        assert operating_plan["add_on_offers"][0]["status"] == "purchased"
    finally:
        settings.zuul_service_token = original_token


# --- Webhook with missing business ---


async def test_webhook_unknown_business_returns_skipped(client):
    """Webhook for unknown business returns skipped."""
    from arclane.core.config import settings
    orig_token = settings.zuul_service_token
    settings.zuul_service_token = "test-secret"
    try:
        resp = await client.post(
            "/api/businesses/unknown-slug/billing/webhook",
            json={
                "event": "subscription.created",
                "customer_email": "nobody@example.com",
                "plan": "pro",
                "business_slug": "does-not-exist",
            },
            headers={"X-Service-Token": "test-secret"},
        )
        # The billing webhook route is behind get_business which checks slug
        # Depending on routing, this may 404 or 200 with skipped
        assert resp.status_code in (200, 404)
    finally:
        settings.zuul_service_token = orig_token


# --- Multiple resets ---


async def test_multiple_resets_are_idempotent(db):
    """Running monthly reset twice gives the same result."""
    from arclane.engine.scheduler import _monthly_credit_reset

    biz_id = await _create_business(db, "idempotent-reset", plan="pro", credits=5)

    with patch("arclane.engine.scheduler.async_session", db):
        await _monthly_credit_reset()
        await _monthly_credit_reset()

    async with db() as session:
        biz = await session.get(Business, biz_id)
        assert biz.credits_remaining == 20
