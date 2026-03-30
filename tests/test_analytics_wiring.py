"""Tests for analytics engine wiring and data-driven insights."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from arclane.models.tables import Base, Business, Content, Cycle


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
        slug="analytics-test",
        name="Analytics Test",
        description="Testing analytics",
        owner_email="test@example.com",
        plan="pro",
        working_days_remaining=20,
        roadmap_day=15,
        current_phase=1,
        health_score=55.0,
    )
    session.add(biz)
    await session.commit()
    await session.refresh(biz)
    return biz


class TestAnalyticsEngineImport:
    def test_analytics_engine_importable(self):
        from arclane.analytics.engine import AnalyticsEngine
        engine = AnalyticsEngine()
        assert engine is not None

    def test_insights_singleton_importable(self):
        from arclane.api.routes.insights import _engine
        assert _engine is not None


class TestAnalyticsIngestion:
    def test_record_insight_round_trip(self):
        from arclane.analytics.engine import AnalyticsEngine, CustomerInsight
        engine = AnalyticsEngine()
        insight = CustomerInsight(
            business_id=1, plan="pro", lifetime_value_cents=9900,
            months_active=1, total_cycles=3, total_content=5,
            features_used=4, engagement_score=65.0,
            churn_risk=0.35, expansion_potential=0.5,
        )
        result = engine.record_insight(insight)
        assert result.business_id == 1
        fetched = engine.get_insight(1)
        assert fetched is not None
        assert fetched.plan == "pro"

    def test_record_journey_event_round_trip(self):
        from arclane.analytics.engine import AnalyticsEngine
        engine = AnalyticsEngine()
        journey = engine.record_journey_event(
            1, "onboarding", "cycle_complete",
            metadata={"roadmap_day": 5, "cycle_id": 42},
        )
        assert journey.current_stage == "onboarding"
        assert len(journey.events) == 1
        assert journey.events[0].action == "cycle_complete"

    def test_insights_summary_populated(self):
        from arclane.analytics.engine import AnalyticsEngine, CustomerInsight
        engine = AnalyticsEngine()
        for i in range(3):
            engine.record_insight(CustomerInsight(
                business_id=i, plan="starter", lifetime_value_cents=4900,
                months_active=2, total_cycles=5, total_content=10,
                features_used=3, engagement_score=60.0,
                churn_risk=0.4, expansion_potential=0.3,
            ))
        summary = engine.get_insights_summary()
        assert summary["total_customers"] == 3
        assert summary["avg_ltv_cents"] == 4900


class TestContentCalendarRoute:
    async def test_auto_fill_endpoint_exists(self, session, business):
        """The auto-fill endpoint should be registered on the content router."""
        from arclane.api.routes.content import router
        paths = [r.path for r in router.routes]
        assert "/auto-fill" in paths


class TestWebhookRoutes:
    def test_all_webhook_routes_registered(self):
        from arclane.api.routes.webhooks import router
        paths = [r.path for r in router.routes]
        assert len(paths) >= 5
        expected = {"/content-performance", "/leads", "/distribution-feedback", "/metrics", "/revenue"}
        assert expected.issubset(set(paths))


class TestForecastRoutes:
    def test_forecast_routes_registered(self):
        from arclane.api.routes.forecast import router
        paths = [r.path for r in router.routes]
        assert "" in paths  # GET /
        assert "/pace" in paths
        assert "/bottlenecks" in paths


class TestRepurposeRoutes:
    def test_repurpose_routes_registered(self):
        from arclane.api.routes.repurpose import router
        paths = [r.path for r in router.routes]
        assert "/{content_id}/formats" in paths
        assert "/{content_id}/repurpose/{target_format}" in paths


class TestContentAnalyticsRoutes:
    def test_content_analytics_routes_registered(self):
        from arclane.api.routes.content_analytics import router
        paths = [r.path for r in router.routes]
        assert "/{content_id}/performance" in paths
        assert "/top" in paths
        assert "/by-type" in paths
        assert "/insights" in paths


class TestBenchmarkService:
    async def test_percentile_edge_cases(self):
        from arclane.services.benchmarks import _calculate_percentile, _median
        # Empty cohort
        assert _calculate_percentile(50, []) == 50
        # Single value cohort
        assert _calculate_percentile(50, [50]) >= 1
        # Median of empty
        assert _median([]) == 0
        # Median of single
        assert _median([42]) == 42
        # Median of even
        assert _median([10, 20]) == 15


class TestContentRepurposerEdgeCases:
    def test_repurpose_empty_body(self):
        from arclane.services.content_repurposer import repurpose
        result = repurpose("blog", "Title", "", "twitter_thread")
        assert result["format"] == "twitter_thread"
        assert len(result["pieces"]) >= 1

    def test_repurpose_very_long_body(self):
        from arclane.services.content_repurposer import repurpose
        long_body = "This is a sentence about business growth. " * 100
        result = repurpose("blog", "Long Post", long_body, "linkedin_carousel")
        assert result["format"] == "linkedin_carousel"
        # Should cap at reasonable slide count
        assert result["piece_count"] <= 12

    def test_quote_cards_format(self):
        from arclane.services.content_repurposer import repurpose
        body = "Great businesses start with a clear offer. The best marketing is honest marketing. Revenue follows value creation."
        result = repurpose("blog", "Quotes", body, "quote_cards")
        assert result["format"] == "quote_cards"
        assert all('"' in q for q in result["pieces"])

    def test_key_takeaways_format(self):
        from arclane.services.content_repurposer import repurpose
        body = "First you need to validate the idea. Then you need to build distribution. Finally you need to optimize conversion. Each step builds on the last one."
        result = repurpose("blog", "Steps", body, "key_takeaways")
        assert result["format"] == "key_takeaways"
        assert len(result["pieces"]) >= 2


class TestPhaseContextBlock:
    def test_all_phases_have_context(self):
        from arclane.engine.executive_prompts import phase_context_block
        for phase in range(1, 6):
            block = phase_context_block(phase, 50)
            assert len(block) > 0

    def test_phase_context_includes_health(self):
        from arclane.engine.executive_prompts import phase_context_block
        block = phase_context_block(3, 60, health_score=72.5)
        assert "72" in block or "73" in block  # Rounded

    def test_phase_0_returns_empty(self):
        from arclane.engine.executive_prompts import phase_context_block
        assert phase_context_block(0, 0) == ""


class TestSchedulerFunctions:
    def test_scheduler_has_all_jobs(self):
        """Verify all scheduler jobs are defined."""
        from arclane.engine import scheduler
        assert hasattr(scheduler, '_nightly_cycle')
        assert hasattr(scheduler, '_monthly_working_day_reset')
        assert hasattr(scheduler, '_advance_roadmap_days')
        assert hasattr(scheduler, '_send_weekly_digests')
        assert hasattr(scheduler, '_publish_scheduled_content')
        assert hasattr(scheduler, '_recover_stuck_cycles')
        assert hasattr(scheduler, '_container_health_check')
