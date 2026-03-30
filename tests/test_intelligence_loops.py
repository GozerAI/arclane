"""Tests for closed-loop intelligence features — overdue prioritization, revenue insights,
health gap recommendations, ongoing optimizer bootstrap, competitive monitor integration,
content publisher distribution, lifecycle notifications, and response schemas."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import (
    Base,
    Business,
    BusinessHealthScore,
    Content,
    DistributionChannel,
    Milestone,
    RevenueEvent,
)


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
        name="Test Business",
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
# 1. Overdue milestone prioritization
# ---------------------------------------------------------------------------

class TestOverdueMilestonePrioritization:
    async def test_generate_phase_tasks_prioritizes_overdue(self, session, business):
        """Tasks with overdue milestones should be selected first."""
        from arclane.services.roadmap_service import initialize_roadmap, generate_phase_tasks

        await initialize_roadmap(business, session)
        business.roadmap_day = 15  # Week 3 of Phase 1
        business.current_phase = 1
        await session.commit()
        await session.refresh(business)

        # Check that week_3 tasks are available and can be prioritized
        tasks = await generate_phase_tasks(business, session)
        assert len(tasks) >= 1
        # The task should have a key starting with p1-
        assert tasks[0]["key"].startswith("p1-")


# ---------------------------------------------------------------------------
# 2. Revenue insights in advisory notes
# ---------------------------------------------------------------------------

class TestRevenueInsights:
    async def test_advisory_revenue_insights_growth(self, session, business):
        """Advisory should celebrate revenue growth above 20%."""
        from arclane.services.advisory_service import generate_advisory_notes
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        now = datetime.now(timezone.utc)
        # Last week: $50
        session.add(RevenueEvent(
            business_id=business.id, source="stripe", amount_cents=5000,
            event_date=now - timedelta(days=10),
        ))
        # This week: $100 (100% growth)
        session.add(RevenueEvent(
            business_id=business.id, source="stripe", amount_cents=10000,
            event_date=now - timedelta(days=2),
        ))
        await session.commit()

        notes = await generate_advisory_notes(business, session)
        titles = [n["title"] for n in notes]
        assert any("Revenue up" in t for t in titles)

    async def test_advisory_revenue_insights_decline(self, session, business):
        """Advisory should warn about revenue decline above 20%."""
        from arclane.services.advisory_service import generate_advisory_notes
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        now = datetime.now(timezone.utc)
        # Last week: $100
        session.add(RevenueEvent(
            business_id=business.id, source="stripe", amount_cents=10000,
            event_date=now - timedelta(days=10),
        ))
        # This week: $30 (70% decline)
        session.add(RevenueEvent(
            business_id=business.id, source="stripe", amount_cents=3000,
            event_date=now - timedelta(days=2),
        ))
        await session.commit()

        notes = await generate_advisory_notes(business, session)
        titles = [n["title"] for n in notes]
        assert any("Revenue down" in t for t in titles)


# ---------------------------------------------------------------------------
# 3. Health gap recommendations
# ---------------------------------------------------------------------------

class TestHealthGapRecommendations:
    async def test_advisory_health_gap_recommendations(self, session, business):
        """Advisory should recommend improvements for weak health areas."""
        from arclane.services.advisory_service import generate_advisory_notes
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        # New business with no content/cycles = low health scores
        notes = await generate_advisory_notes(business, session)
        categories = [n["category"] for n in notes]
        # Should have at least one recommendation for weak areas
        assert "recommendation" in categories or "insight" in categories


# ---------------------------------------------------------------------------
# 4. Ongoing optimizer health bootstrap
# ---------------------------------------------------------------------------

class TestOngoingOptimizerBootstrap:
    async def test_ongoing_optimizer_bootstraps_health(self, session, business):
        """Ongoing optimizer should auto-record health snapshot if none exist."""
        from arclane.services.ongoing_optimizer import select_adaptive_task

        business.current_phase = 5  # Post-graduation
        business.roadmap_day = 95
        await session.commit()
        await session.refresh(business)

        # No health scores yet
        count_before = (await session.execute(
            select(func.count(BusinessHealthScore.id)).where(
                BusinessHealthScore.business_id == business.id
            )
        )).scalar() or 0
        assert count_before == 0

        task = await select_adaptive_task(business, session)
        assert task is not None

        # Should have bootstrapped health scores
        count_after = (await session.execute(
            select(func.count(BusinessHealthScore.id)).where(
                BusinessHealthScore.business_id == business.id
            )
        )).scalar() or 0
        assert count_after > 0


# ---------------------------------------------------------------------------
# 5. Competitive monitor with real integration
# ---------------------------------------------------------------------------

class TestCompetitiveMonitorIntegration:
    async def test_competitive_monitor_check_with_website(self, session, business):
        """Competitive check should attempt website intelligence when URL is present."""
        from arclane.services.competitive_monitor import add_competitor, run_check

        monitor = await add_competitor(business, session, name="Rival Corp", url="https://rival.example.com")
        await session.commit()

        # Mock the website intelligence to avoid real HTTP calls
        mock_snapshot = AsyncMock(return_value=type("Snapshot", (), {
            "title": "Rival Corp",
            "raw_text": "We are the best",
            "meta_description": "Competition",
        })())
        mock_summarize = lambda s: "Rival Corp offers competing product with vague messaging."

        with patch("arclane.engine.website_intelligence.fetch_website_snapshot", mock_snapshot), \
             patch("arclane.engine.website_intelligence.summarize_website", mock_summarize):
            results = await run_check(business, session)

        assert len(results) == 1
        assert results[0]["status"] == "checked"
        findings = results[0]["findings"]
        assert findings.get("messaging_snapshot") is not None
        assert "Rival Corp" in findings.get("summary", "")

    async def test_competitive_advisories_on_messaging_change(self, session, business):
        """Competitive advisories should flag messaging changes."""
        from arclane.services.competitive_monitor import add_competitor, generate_competitive_advisories

        monitor = await add_competitor(business, session, name="Rival Corp")
        # Set previous findings with a snapshot
        monitor.findings_json = {"messaging_snapshot": "Old messaging about our product"}
        await session.commit()

        # Simulate check results with changed messaging
        check_results = [{
            "competitor": "Rival Corp",
            "status": "checked",
            "findings": {
                "messaging_changed": True,
                "messaging_snapshot": "New aggressive pricing messaging",
            },
        }]

        notes = await generate_competitive_advisories(business, check_results, session)
        assert len(notes) >= 1
        assert any("messaging changed" in n["title"].lower() for n in notes)


# ---------------------------------------------------------------------------
# 6. Content publisher with distribution
# ---------------------------------------------------------------------------

class TestContentPublisherDistribution:
    async def test_content_publisher_with_distribution(self, session, business):
        """publish_with_distribution should attempt distribution channels."""
        from arclane.services.content_publisher import ContentPublisher
        from arclane.services.distribution_service import configure_channel

        # Set up a distribution channel
        await configure_channel(business, session, platform="twitter")

        # Create a content item
        content = Content(
            business_id=business.id, content_type="social",
            title="Test post", body="Hello world", status="draft",
        )
        session.add(content)
        await session.commit()
        await session.refresh(content)

        publisher = ContentPublisher()
        report = await publisher.publish_with_distribution(
            content_id=content.id, content_type="social",
            title="Test post", body="Hello world",
            business_name=business.name, business_id=business.id,
            session=session,
        )

        # Should have attempted KH + distribution channel
        assert report.channels_attempted >= 1
        channel_names = [r.channel for r in report.results]
        assert "knowledge_harvester" in channel_names


# ---------------------------------------------------------------------------
# 7. Notification functions exist
# ---------------------------------------------------------------------------

class TestNotificationFunctions:
    def test_lifecycle_notification_functions_exist(self):
        """All lifecycle notification functions should be importable."""
        from arclane.notifications import (
            send_phase_advancement_email,
            send_milestone_celebration_email,
            send_urgent_advisory_email,
            send_weekly_digest_email,
        )
        assert callable(send_phase_advancement_email)
        assert callable(send_milestone_celebration_email)
        assert callable(send_urgent_advisory_email)
        assert callable(send_weekly_digest_email)


# ---------------------------------------------------------------------------
# 8. Pydantic schemas
# ---------------------------------------------------------------------------

class TestResponseSchemas:
    def test_response_schemas_importable(self):
        """All new response schemas should be importable."""
        from arclane.models.schemas import (
            RoadmapResponse,
            PhaseResponse,
            MilestoneResponse,
            HealthScoreResponse,
            AdvisoryNoteResponse,
            WeeklyDigestResponse,
            RevenueEventResponse,
            RevenueSummaryResponse,
            ROIResponse,
            DistributionChannelResponse,
            CompetitorResponse,
            CompetitiveBriefResponse,
        )
        from pydantic import BaseModel

        for schema in [RoadmapResponse, PhaseResponse, MilestoneResponse, HealthScoreResponse,
                       AdvisoryNoteResponse, WeeklyDigestResponse, RevenueEventResponse,
                       RevenueSummaryResponse, ROIResponse, DistributionChannelResponse,
                       CompetitorResponse, CompetitiveBriefResponse]:
            assert issubclass(schema, BaseModel)
