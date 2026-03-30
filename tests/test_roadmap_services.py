"""Tests for roadmap services — roadmap, health, advisory, revenue, distribution, competitive, optimizer, calendar."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import (
    Base,
    Business,
    BusinessHealthScore,
    Content,
    Cycle,
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
# roadmap_service
# ---------------------------------------------------------------------------

class TestRoadmapService:
    """Tests for arclane.services.roadmap_service."""

    async def test_get_phase_for_day(self):
        from arclane.services.roadmap_service import get_phase_for_day

        assert get_phase_for_day(0) == 0
        assert get_phase_for_day(1) == 1
        assert get_phase_for_day(15) == 1
        assert get_phase_for_day(16) == 2
        assert get_phase_for_day(30) == 2
        assert get_phase_for_day(31) == 3
        assert get_phase_for_day(45) == 3
        assert get_phase_for_day(46) == 4
        assert get_phase_for_day(60) == 4
        assert get_phase_for_day(61) == 5

    async def test_initialize_roadmap(self, session, business):
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        await session.commit()

        assert business.roadmap_day == 1
        assert business.current_phase == 1

        from sqlalchemy import select
        from arclane.models.tables import RoadmapPhase

        phases = (await session.execute(
            select(RoadmapPhase).where(RoadmapPhase.business_id == business.id)
        )).scalars().all()
        assert len(phases) == 4
        active = [p for p in phases if p.status == "active"]
        assert len(active) == 1
        assert active[0].phase_number == 1

        milestones = (await session.execute(
            select(Milestone).where(Milestone.business_id == business.id)
        )).scalars().all()
        # 18 + 15 + 15 + 15 = 63 total milestones across 4 phases
        assert len(milestones) == 63

    async def test_generate_phase_tasks_day10(self, session, business):
        from arclane.services.roadmap_service import initialize_roadmap, generate_phase_tasks

        await initialize_roadmap(business, session)
        business.roadmap_day = 10
        business.current_phase = 1
        await session.commit()

        tasks = await generate_phase_tasks(business, session)
        # generate_phase_tasks picks one task per cycle — first task on day_10
        assert len(tasks) == 1
        assert tasks[0]["key"] == "p1-competitor-03"
        assert tasks[0]["kind"] == "roadmap"
        assert tasks[0]["phase"] == 1

    async def test_generate_phase_tasks_skips_completed(self, session, business):
        from arclane.services.roadmap_service import initialize_roadmap, generate_phase_tasks

        await initialize_roadmap(business, session)
        business.roadmap_day = 10
        business.current_phase = 1

        # Simulate that p1-competitor-03 (the day_10 task) was already done
        from sqlalchemy import select as sa_select
        from arclane.models.tables import Milestone as MilestoneModel
        result = await session.execute(
            sa_select(MilestoneModel).where(
                MilestoneModel.business_id == business.id,
                MilestoneModel.key == "p1-competitor-03",
            )
        )
        ms = result.scalar_one()
        ms.status = "completed"
        ms.completed_at = datetime.now(timezone.utc)
        await session.commit()

        tasks = await generate_phase_tasks(business, session)
        # p1-competitor-03 is the only task on day_10; when done, no tasks returned for that day
        assert len(tasks) == 0

    async def test_check_phase_graduation_not_ready(self, session, business):
        from arclane.services.roadmap_service import initialize_roadmap, check_phase_graduation

        await initialize_roadmap(business, session)
        await session.commit()

        check = await check_phase_graduation(business, session)
        assert check["ready"] is False
        assert len(check["unmet"]) > 0

    async def test_check_phase_graduation_ready(self, session, business):
        from arclane.services.roadmap_service import (
            initialize_roadmap,
            check_phase_graduation,
            complete_milestone,
        )

        await initialize_roadmap(business, session)
        await session.commit()

        # Complete all required phase-1 milestones
        required = [
            "p1-strategy-brief", "p1-market-research",
            "p1-landing-page-draft", "p1-icp-document", "p1-lead-capture",
            "p1-financial-model", "p1-graduation-check",
        ]
        for key in required:
            await complete_milestone(business, key, session)

        # Add enough content to meet the minimum (5)
        for i in range(5):
            session.add(Content(
                business_id=business.id,
                content_type="blog",
                body=f"Test content {i}",
                status="published",
            ))
        await session.commit()

        check = await check_phase_graduation(business, session)
        assert check["ready"] is True
        assert check["score"] == 100.0

    async def test_advance_phase(self, session, business):
        from arclane.services.roadmap_service import (
            initialize_roadmap,
            advance_phase,
            complete_milestone,
        )

        await initialize_roadmap(business, session)
        await session.commit()

        # Satisfy phase 1 graduation criteria
        for key in ["p1-strategy-brief", "p1-market-research",
                     "p1-landing-page-draft", "p1-icp-document", "p1-lead-capture",
                     "p1-financial-model", "p1-graduation-check"]:
            await complete_milestone(business, key, session)
        for i in range(5):
            session.add(Content(
                business_id=business.id, content_type="blog",
                body=f"c{i}", status="published",
            ))
        await session.commit()

        result = await advance_phase(business, session)
        assert result["advanced"] is True
        assert result["from_phase"] == 1
        assert result["to_phase"] == 2
        assert business.current_phase == 2

    async def test_advance_to_graduation(self, session, business):
        from arclane.services.roadmap_service import (
            initialize_roadmap,
            advance_phase,
            complete_milestone,
        )

        await initialize_roadmap(business, session)
        business.current_phase = 4
        await session.commit()

        # Complete phase 4 required milestones
        for key in ["p4-60day-report-b", "p4-q2-plan-b", "p4-graduation"]:
            await complete_milestone(business, key, session)

        # Meet content minimum (22)
        for i in range(22):
            session.add(Content(
                business_id=business.id, content_type="blog",
                body=f"c{i}", status="published",
            ))
        await session.commit()

        result = await advance_phase(business, session)
        assert result["advanced"] is True
        assert result["to_phase"] == 5
        assert result.get("graduated") is True
        assert business.graduation_date is not None

    async def test_complete_milestone(self, session, business):
        from arclane.services.roadmap_service import initialize_roadmap, complete_milestone

        await initialize_roadmap(business, session)
        await session.commit()

        ok = await complete_milestone(
            business, "p1-strategy-brief", session,
            evidence={"source": "cycle-123"},
        )
        assert ok is True

        from sqlalchemy import select
        m = (await session.execute(
            select(Milestone).where(Milestone.key == "p1-strategy-brief")
        )).scalar_one()
        assert m.status == "completed"
        assert m.completed_at is not None
        assert m.evidence_json == {"source": "cycle-123"}

    async def test_get_roadmap_summary(self, session, business):
        from arclane.services.roadmap_service import initialize_roadmap, get_roadmap_summary

        await initialize_roadmap(business, session)
        await session.commit()

        summary = await get_roadmap_summary(business, session)
        assert summary["roadmap_day"] == 1
        assert summary["current_phase"] == 1
        assert summary["total_days"] == 60
        assert len(summary["phases"]) == 4
        assert summary["phases"][0]["phase_name"] == "Foundation"
        assert summary["phases"][0]["milestones_total"] == 18

    async def test_get_next_actions(self, session, business):
        from arclane.services.roadmap_service import initialize_roadmap, get_next_actions

        await initialize_roadmap(business, session)
        business.roadmap_day = 10
        await session.commit()

        actions = await get_next_actions(business, session)
        assert len(actions) > 0
        # Should contain pending milestones
        keys = [a.get("milestone_key") for a in actions if "milestone_key" in a]
        assert len(keys) > 0


# ---------------------------------------------------------------------------
# health_score_service
# ---------------------------------------------------------------------------

class TestHealthScoreService:
    """Tests for arclane.services.health_score_service."""

    async def test_calculate_health_score_new_business(self, session, business):
        from arclane.services.health_score_service import calculate_health_score

        business.current_phase = 1
        await session.commit()

        result = await calculate_health_score(business, session)
        assert "overall" in result
        assert "sub_scores" in result
        assert "factors" in result
        # New business gets base scores — overall should be positive
        assert result["overall"] > 0
        for key in ("market_fit", "content", "revenue", "operations", "momentum"):
            assert key in result["sub_scores"]

    async def test_record_health_snapshot(self, session, business):
        from arclane.services.health_score_service import record_health_snapshot
        from sqlalchemy import select

        business.current_phase = 1
        await session.commit()

        score = await record_health_snapshot(business, session)
        await session.commit()

        assert isinstance(score, float)
        assert business.health_score == score

        rows = (await session.execute(
            select(BusinessHealthScore).where(BusinessHealthScore.business_id == business.id)
        )).scalars().all()
        # 1 overall + 5 sub-scores = 6 records
        assert len(rows) == 6
        types = {r.score_type for r in rows}
        assert "overall" in types
        assert "content" in types

    async def test_get_health_trend(self, session, business):
        from arclane.services.health_score_service import record_health_snapshot, get_health_trend

        business.current_phase = 1
        await session.commit()

        # Record two snapshots
        await record_health_snapshot(business, session)
        await session.commit()
        await record_health_snapshot(business, session)
        await session.commit()

        trend = await get_health_trend(business, session, days=30)
        assert len(trend) == 2
        assert "score" in trend[0]
        assert "recorded_at" in trend[0]


# ---------------------------------------------------------------------------
# advisory_service
# ---------------------------------------------------------------------------

class TestAdvisoryService:
    """Tests for arclane.services.advisory_service."""

    async def test_generate_advisory_notes_low_working_days(self, session, business):
        from arclane.services.advisory_service import generate_advisory_notes
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        business.working_days_remaining = 2
        business.working_days_bonus = 0
        await session.commit()

        notes = await generate_advisory_notes(business, session)
        wd_warnings = [n for n in notes if "Working days running low" in n["title"]]
        assert len(wd_warnings) == 1
        assert wd_warnings[0]["category"] == "warning"

    async def test_generate_advisory_notes_overdue_milestones(self, session, business):
        from arclane.services.advisory_service import generate_advisory_notes
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        # Day 25 means all phase 1 milestones (due_day <= 21) are overdue
        business.roadmap_day = 25
        business.current_phase = 1
        await session.commit()

        notes = await generate_advisory_notes(business, session)
        overdue_notes = [n for n in notes if n["category"] == "warning" and "Overdue" in n["title"]]
        assert len(overdue_notes) > 0

    async def test_generate_weekly_digest(self, session, business):
        from arclane.services.advisory_service import generate_weekly_digest

        business.roadmap_day = 14
        business.current_phase = 1
        await session.commit()

        digest = await generate_weekly_digest(business, session)
        assert "period" in digest
        assert "cycles" in digest
        assert "content" in digest
        assert "milestones" in digest
        assert "revenue" in digest
        assert digest["roadmap_day"] == 14

    async def test_check_warning_conditions(self, session, business):
        from arclane.services.advisory_service import check_warning_conditions

        business.working_days_remaining = 0
        business.working_days_bonus = 0
        business.current_phase = 1
        business.roadmap_day = 30  # Past phase 1 end (day 21)
        await session.commit()

        warnings = await check_warning_conditions(business, session)
        assert any("No working days" in w for w in warnings)
        assert any("Phase 1 should have completed" in w for w in warnings)


# ---------------------------------------------------------------------------
# revenue_tracker
# ---------------------------------------------------------------------------

class TestRevenueTracker:
    """Tests for arclane.services.revenue_tracker."""

    async def test_record_revenue_event(self, session, business):
        from arclane.services.revenue_tracker import record_revenue_event

        event = await record_revenue_event(
            business, session,
            source="stripe",
            amount_cents=5000,
            attribution={"utm_source": "google"},
        )
        await session.commit()

        assert event.id is not None
        assert event.source == "stripe"
        assert event.amount_cents == 5000
        assert event.currency == "usd"
        assert event.attribution_json == {"utm_source": "google"}

    async def test_get_revenue_summary(self, session, business):
        from arclane.services.revenue_tracker import record_revenue_event, get_revenue_summary

        await record_revenue_event(business, session, source="stripe", amount_cents=5000)
        await record_revenue_event(business, session, source="gumroad", amount_cents=3000)
        await record_revenue_event(business, session, source="stripe", amount_cents=2000)
        await session.commit()

        summary = await get_revenue_summary(business, session)
        assert summary["total_cents"] == 10000
        assert summary["total_usd"] == 100.0
        assert summary["total_events"] == 3
        assert "stripe" in summary["by_source"]
        assert summary["by_source"]["stripe"]["total_cents"] == 7000
        assert summary["by_source"]["gumroad"]["events"] == 1

    async def test_calculate_roi(self, session, business):
        from arclane.services.revenue_tracker import record_revenue_event, calculate_roi

        business.roadmap_day = 30  # ~1 month active
        await record_revenue_event(business, session, source="stripe", amount_cents=20000)
        await session.commit()

        roi = await calculate_roi(business, session)
        assert roi["total_revenue_cents"] == 20000
        assert roi["plan"] == "pro"
        # pro plan = $99/month, 1 month => cost = 9900 cents
        assert roi["estimated_cost_cents"] == 9900
        assert roi["roi_pct"] > 0  # 20000 - 9900 = 10100, ROI > 100%


# ---------------------------------------------------------------------------
# distribution_service
# ---------------------------------------------------------------------------

class TestDistributionService:
    """Tests for arclane.services.distribution_service."""

    async def test_configure_channel(self, session, business):
        from arclane.services.distribution_service import configure_channel

        ch = await configure_channel(
            business, session,
            platform="twitter",
            config={"api_key": "test123"},
        )
        await session.commit()

        assert ch.id is not None
        assert ch.platform == "twitter"
        assert ch.status == "active"
        assert ch.config_json == {"api_key": "test123"}

    async def test_configure_channel_update(self, session, business):
        from arclane.services.distribution_service import configure_channel

        ch1 = await configure_channel(business, session, platform="twitter", config={"v": 1})
        await session.commit()

        ch2 = await configure_channel(business, session, platform="twitter", config={"v": 2})
        await session.commit()

        assert ch1.id == ch2.id
        assert ch2.config_json == {"v": 2}

    async def test_distribute_content(self, session, business):
        from arclane.services.distribution_service import configure_channel, distribute_content

        await configure_channel(business, session, platform="twitter")
        await configure_channel(business, session, platform="linkedin")

        content = Content(
            business_id=business.id,
            content_type="social",
            body="Hello world",
            status="published",
        )
        session.add(content)
        await session.commit()
        await session.refresh(content)

        result = await distribute_content(business, content, session)
        await session.commit()

        assert result["content_id"] == content.id
        assert "twitter" in result["channels"]
        assert result["channels"]["twitter"]["status"] == "distributed"
        assert "linkedin" in result["channels"]
        assert content.distribution_status == "distributed"

    async def test_get_distribution_stats(self, session, business):
        from arclane.services.distribution_service import configure_channel, get_distribution_stats

        await configure_channel(business, session, platform="twitter")
        session.add(Content(
            business_id=business.id, content_type="blog", body="test",
            distribution_status="distributed",
        ))
        session.add(Content(
            business_id=business.id, content_type="social", body="test",
            distribution_status="pending",
        ))
        await session.commit()

        stats = await get_distribution_stats(business, session)
        assert stats["channel_count"] == 1
        assert stats["content_distributed"] == 1
        assert stats["content_pending"] == 1
        assert stats["content_total"] == 2


# ---------------------------------------------------------------------------
# competitive_monitor
# ---------------------------------------------------------------------------

class TestCompetitiveMonitor:
    """Tests for arclane.services.competitive_monitor."""

    async def test_add_competitor(self, session, business):
        from arclane.services.competitive_monitor import add_competitor

        monitor = await add_competitor(
            business, session,
            name="Acme Corp",
            url="https://acme.com",
        )
        await session.commit()

        assert monitor.id is not None
        assert monitor.competitor_name == "Acme Corp"
        assert monitor.competitor_url == "https://acme.com"

    async def test_add_competitor_dedup(self, session, business):
        from arclane.services.competitive_monitor import add_competitor

        m1 = await add_competitor(business, session, name="Acme Corp", url="https://acme.com")
        await session.commit()

        m2 = await add_competitor(business, session, name="Acme Corp", url="https://acme.io")
        await session.commit()

        assert m1.id == m2.id
        assert m2.competitor_url == "https://acme.io"

    async def test_get_competitive_brief(self, session, business):
        from arclane.services.competitive_monitor import add_competitor, get_competitive_brief

        await add_competitor(business, session, name="Acme Corp", url="https://acme.com")
        await add_competitor(business, session, name="Rival Inc", url="https://rival.com")
        await session.commit()

        brief = await get_competitive_brief(business, session)
        assert brief["business"] == "Test Business"
        assert brief["competitors_tracked"] == 2
        assert len(brief["competitors"]) == 2
        names = {c["name"] for c in brief["competitors"]}
        assert names == {"Acme Corp", "Rival Inc"}


# ---------------------------------------------------------------------------
# ongoing_optimizer
# ---------------------------------------------------------------------------

class TestOngoingOptimizer:
    """Tests for arclane.services.ongoing_optimizer."""

    async def test_select_adaptive_task(self, session, business):
        from arclane.services.ongoing_optimizer import select_adaptive_task

        business.current_phase = 5
        business.roadmap_day = 95
        await session.commit()

        task = await select_adaptive_task(business, session)
        assert task is not None
        assert task["kind"] == "ongoing"
        assert task["phase"] == 5
        assert "key" in task
        assert "description" in task

    async def test_calculate_task_priority(self):
        from arclane.services.ongoing_optimizer import _calculate_task_priority

        template = {"score_weight": {"content": 0.4, "momentum": 0.3}}

        # Low scores => high priority
        health_low = {"content": 20.0, "momentum": 30.0}
        priority_low = _calculate_task_priority(template, health_low)

        # High scores => low priority
        health_high = {"content": 80.0, "momentum": 90.0}
        priority_high = _calculate_task_priority(template, health_high)

        assert priority_low > priority_high


# ---------------------------------------------------------------------------
# content_calendar
# ---------------------------------------------------------------------------

class TestContentCalendar:
    """Tests for arclane.services.content_calendar."""

    async def test_generate_calendar(self, session, business):
        from arclane.services.content_calendar import generate_calendar

        business.current_phase = 2
        business.roadmap_day = 30
        await session.commit()

        cal = await generate_calendar(business, session, days=14)
        assert cal["business"] == "Test Business"
        assert "period" in cal
        assert cal["total_slots"] > 0
        assert "slots" in cal
        # All slots should be open since no content is scheduled
        assert cal["open"] == cal["total_slots"]

    async def test_get_gaps(self, session, business):
        from arclane.services.content_calendar import get_gaps

        # Add some blog content but no social/newsletter/report
        for i in range(2):
            session.add(Content(
                business_id=business.id,
                content_type="blog",
                body=f"Blog post {i}",
                status="published",
            ))
        await session.commit()

        gaps = await get_gaps(business, session)
        assert len(gaps) > 0
        types = {g["content_type"] for g in gaps}
        # Should identify gaps in social, newsletter, report (and blog since 2 < 4)
        assert "social" in types
        assert "newsletter" in types
        # Blog has 2 but target is 4 so it should also appear
        assert "blog" in types
        # Gaps are sorted by deficit descending
        assert gaps[0]["deficit"] >= gaps[-1]["deficit"]
