"""Tests for newest features — calendar auto-fill, content attribution, webhooks, phase add-ons, draft generation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business, Content


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def business(session):
    biz = Business(
        slug="testbiz",
        name="TestBiz",
        description="A test business for automated testing",
        owner_email="test@example.com",
        plan="pro",
        working_days_remaining=20,
        working_days_bonus=0,
        roadmap_day=0,
        current_phase=0,
    )
    session.add(biz)
    await session.commit()
    await session.refresh(biz)
    return biz


# ---------------------------------------------------------------------------
# 1. Content Calendar Auto-Fill
# ---------------------------------------------------------------------------

class TestContentCalendarAutoFill:

    async def test_auto_fill_calendar_creates_drafts(self, session, business):
        """auto_fill_calendar should create draft content for open slots."""
        from arclane.services.roadmap_service import initialize_roadmap
        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        from arclane.services.content_calendar import auto_fill_calendar
        created = await auto_fill_calendar(business, session, days_ahead=7, max_drafts=3)
        await session.commit()

        assert len(created) > 0
        assert len(created) <= 3
        for item in created:
            assert "content_type" in item
            assert "title" in item

        # Verify content was persisted
        from sqlalchemy import select, func
        count = (await session.execute(
            select(func.count(Content.id)).where(
                Content.business_id == business.id, Content.status == "draft",
            )
        )).scalar() or 0
        assert count >= len(created)

    async def test_auto_fill_respects_max_drafts(self, session, business):
        """auto_fill_calendar should not exceed max_drafts."""
        from arclane.services.roadmap_service import initialize_roadmap
        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        from arclane.services.content_calendar import auto_fill_calendar
        created = await auto_fill_calendar(business, session, days_ahead=30, max_drafts=2)
        assert len(created) <= 2

    async def test_auto_fill_tags_metadata(self, session, business):
        """Auto-filled content should have auto_generated metadata."""
        from arclane.services.roadmap_service import initialize_roadmap
        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        from arclane.services.content_calendar import auto_fill_calendar
        await auto_fill_calendar(business, session, days_ahead=7, max_drafts=1)
        await session.commit()

        from sqlalchemy import select
        result = await session.execute(
            select(Content).where(
                Content.business_id == business.id, Content.status == "draft",
            ).limit(1)
        )
        content = result.scalar_one_or_none()
        assert content is not None
        assert content.metadata_json is not None
        assert content.metadata_json.get("auto_generated") is True


# ---------------------------------------------------------------------------
# 2. Content Attribution
# ---------------------------------------------------------------------------

class TestContentAttribution:

    def test_content_from_result_includes_attribution(self):
        """_content_from_result should tag content with phase and milestone."""
        from unittest.mock import MagicMock
        from arclane.engine.orchestrator import ArclaneOrchestrator

        orch = ArclaneOrchestrator()
        biz = MagicMock()
        biz.id = 1
        biz.current_phase = 2
        biz.roadmap_day = 30

        task_result = {
            "content_type": "report",
            "content_title": "Market Analysis",
            "content_body": "Analysis body text",
            "cycle_id": 42,
            "queue_task_key": "p2-validation-plan",
        }

        content = orch._content_from_result(biz, task_result)
        assert content is not None
        assert content.milestone_key == "p2-validation-plan"
        assert content.metadata_json["phase"] == 2
        assert content.metadata_json["roadmap_day"] == 30
        assert content.metadata_json["cycle_id"] == 42


# ---------------------------------------------------------------------------
# 3. Inbound Webhooks
# ---------------------------------------------------------------------------

class TestInboundWebhooks:

    def test_webhook_routes_importable(self):
        from arclane.api.routes.webhooks import router
        routes = [r.path for r in router.routes]
        assert "/content-performance" in routes
        assert "/leads" in routes
        assert "/distribution-feedback" in routes
        assert "/metrics" in routes
        assert "/revenue" in routes


# ---------------------------------------------------------------------------
# 4. Phase-Aware Add-Ons
# ---------------------------------------------------------------------------

class TestPhaseAwareAddOns:

    def test_enqueue_add_on_stores_phase_context(self):
        """enqueue_add_on should store added_in_phase and added_on_day."""
        from arclane.engine.operating_plan import build_operating_plan, enqueue_add_on

        plan = build_operating_plan(
            name="TestBiz", slug="testbiz",
            description="A test business",
            template="content-site",
        )
        plan["add_on_offers"][0]["status"] = "available"

        updated = enqueue_add_on(plan, "deep-market-dive", phase=2, day=25)

        addon_task = next(t for t in updated["agent_tasks"] if t["kind"] == "add_on")
        assert addon_task["added_in_phase"] == 2
        assert addon_task["added_on_day"] == 25

    def test_enqueue_add_on_defaults_to_zero(self):
        """enqueue_add_on should default phase/day to 0 when not provided."""
        from arclane.engine.operating_plan import build_operating_plan, enqueue_add_on

        plan = build_operating_plan(
            name="TestBiz", slug="testbiz",
            description="A test business",
            template="content-site",
        )
        plan["add_on_offers"][0]["status"] = "available"

        updated = enqueue_add_on(plan, "deep-market-dive")

        addon_task = next(t for t in updated["agent_tasks"] if t["kind"] == "add_on")
        assert addon_task["added_in_phase"] == 0
        assert addon_task["added_on_day"] == 0


# ---------------------------------------------------------------------------
# 5. Draft Generation Quality
# ---------------------------------------------------------------------------

class TestDraftGeneration:

    def test_generate_draft_blog(self):
        from arclane.services.content_calendar import _generate_draft
        title, body = _generate_draft("TestBiz", "A test business", "blog", "Growth tips", 2)
        assert "Growth tips" in title
        assert "TestBiz" in body
        assert "validating" in body.lower()  # Phase 2 context

    def test_generate_draft_social(self):
        from arclane.services.content_calendar import _generate_draft
        title, body = _generate_draft("TestBiz", "A test", "social", "Quick tip", 1)
        assert "Social post" in title
        assert "TestBiz" in body

    def test_generate_draft_newsletter(self):
        from arclane.services.content_calendar import _generate_draft
        title, body = _generate_draft("TestBiz", "A test", "newsletter", "Weekly update", 3)
        assert "TestBiz" in title
        assert "Subject:" in body
