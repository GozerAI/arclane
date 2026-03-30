"""Tests for value-add features — forecaster, repurposer, upsells, benchmarks, content analytics."""

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
# 1. Roadmap Forecaster
# ---------------------------------------------------------------------------

class TestRoadmapForecaster:
    """Tests for arclane.services.roadmap_forecaster."""

    async def test_compute_forecast_new_business(self, session, business):
        """Forecast for a new business should return valid structure."""
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        from arclane.services.roadmap_forecaster import compute_forecast

        forecast = await compute_forecast(business, session)

        assert "velocity" in forecast
        assert "graduation_eta" in forecast
        assert "pace" in forecast
        assert "bottlenecks" in forecast
        assert "weekly_focus" in forecast
        assert "streak" in forecast
        assert forecast["velocity"]["completed"] == 0
        assert forecast["pace"]["status"] in (
            "ahead", "on_track", "slightly_behind", "significantly_behind",
        )

    async def test_compute_forecast_with_progress(self, session, business):
        """Forecast with milestones completed should show velocity."""
        from arclane.services.roadmap_service import initialize_roadmap, complete_milestone

        await initialize_roadmap(business, session)
        business.roadmap_day = 14

        # Complete some milestones
        for key in ["p1-strategy-brief", "p1-landing-page-draft", "p1-growth-asset"]:
            await complete_milestone(business, key, session)

        await session.commit()
        await session.refresh(business)

        from arclane.services.roadmap_forecaster import compute_forecast

        forecast = await compute_forecast(business, session)

        assert forecast["velocity"]["completed"] == 3
        assert forecast["velocity"]["completion_pct"] > 0

    async def test_bottleneck_detection_overdue(self, session, business):
        """Should detect overdue milestones as bottlenecks."""
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        business.roadmap_day = 25  # Past Phase 1 deadline
        business.current_phase = 1
        await session.commit()
        await session.refresh(business)

        from arclane.services.roadmap_forecaster import compute_forecast

        forecast = await compute_forecast(business, session)

        # Should have overdue milestones bottleneck
        bottleneck_types = [b["type"] for b in forecast["bottlenecks"]]
        assert "overdue_milestones" in bottleneck_types

    async def test_weekly_focus_recommendation(self, session, business):
        """Should recommend weekly focus based on phase."""
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        from arclane.services.roadmap_forecaster import compute_forecast

        forecast = await compute_forecast(business, session)

        assert "action" in forecast["weekly_focus"]
        assert "urgency" in forecast["weekly_focus"]


# ---------------------------------------------------------------------------
# 2. Content Repurposer
# ---------------------------------------------------------------------------

class TestContentRepurposer:
    """Tests for arclane.services.content_repurposer."""

    def test_repurpose_blog_to_twitter_thread(self):
        from arclane.services.content_repurposer import repurpose

        result = repurpose(
            "blog", "Test Post",
            "This is a long blog post about business growth. "
            "It has many interesting points about marketing. "
            "The first key insight is about positioning. "
            "The second key insight is about distribution channels. "
            "Always focus on what works.",
            "twitter_thread",
        )
        assert result["format"] == "twitter_thread"
        assert len(result["pieces"]) >= 2
        assert "thread" in result["pieces"][0].lower() or "\U0001f9f5" in result["pieces"][0]

    def test_repurpose_blog_to_linkedin_carousel(self):
        from arclane.services.content_repurposer import repurpose

        result = repurpose(
            "blog", "Growth Tips",
            "Paragraph one about marketing.\nParagraph two about sales.\nParagraph three about retention.",
            "linkedin_carousel",
        )
        assert result["format"] == "linkedin_carousel"
        assert result["piece_count"] >= 3  # Title + content slides + CTA

    def test_repurpose_report_to_executive_summary(self):
        from arclane.services.content_repurposer import repurpose

        result = repurpose(
            "report", "Market Analysis",
            "The market shows strong demand for AI tools. "
            "Competition is increasing but fragmented. "
            "Early movers have an advantage in building trust.",
            "executive_summary",
        )
        assert result["format"] == "executive_summary"
        assert len(result["pieces"]) == 3

    def test_repurpose_to_markdown(self):
        from arclane.services.content_repurposer import repurpose

        result = repurpose("blog", "My Post", "Hello world content", "markdown")
        assert result["format"] == "markdown"
        assert result["body"].startswith("# My Post")

    def test_repurpose_to_email_variant(self):
        from arclane.services.content_repurposer import repurpose

        result = repurpose(
            "blog", "Great Insights",
            "Some body content here about business.",
            "email_variant",
        )
        assert result["format"] == "email_variant"
        assert len(result.get("subject_lines", [])) == 3

    def test_available_formats(self):
        from arclane.services.content_repurposer import available_formats

        blog_formats = available_formats("blog")
        assert len(blog_formats) >= 5
        format_names = [f["format"] for f in blog_formats]
        assert "twitter_thread" in format_names
        assert "linkedin_carousel" in format_names
        assert "markdown" in format_names

    def test_repurpose_unknown_format(self):
        from arclane.services.content_repurposer import repurpose

        result = repurpose("blog", "Test", "Body", "nonexistent_format")
        assert "error" in result


# ---------------------------------------------------------------------------
# 3. Phase-Aware Upsells
# ---------------------------------------------------------------------------

class TestPhaseAwareUpsells:
    """Tests for phase-aware upsell suggestions."""

    def test_phase_suggestions_importable(self):
        """Phase suggestions endpoint and data should be importable."""
        from arclane.api.routes.upsell import _PHASE_SUGGESTIONS

        assert 1 in _PHASE_SUGGESTIONS
        assert 2 in _PHASE_SUGGESTIONS
        assert 3 in _PHASE_SUGGESTIONS
        assert 4 in _PHASE_SUGGESTIONS
        assert 5 in _PHASE_SUGGESTIONS


# ---------------------------------------------------------------------------
# 4. Benchmarks
# ---------------------------------------------------------------------------

class TestBenchmarks:
    """Tests for arclane.services.benchmarks."""

    async def test_compute_benchmarks_single_business(self, session, business):
        """Benchmarks should work even with only 1 business (no cohort)."""
        from arclane.services.roadmap_service import initialize_roadmap

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        from arclane.services.benchmarks import compute_benchmarks

        result = await compute_benchmarks(business, session)

        assert "metrics" in result
        assert "content_total" in result["metrics"]
        assert "percentile" in result["metrics"]["content_total"]
        assert "assessment" in result["metrics"]["content_total"]

    async def test_benchmarks_percentile_calculation(self):
        from arclane.services.benchmarks import _calculate_percentile

        assert _calculate_percentile(50, [10, 20, 30, 40, 60, 70, 80, 90]) > 40
        assert _calculate_percentile(50, [10, 20, 30, 40, 60, 70, 80, 90]) < 60
        assert _calculate_percentile(100, [10, 20, 30]) >= 90
        assert _calculate_percentile(1, [10, 20, 30]) <= 10


# ---------------------------------------------------------------------------
# 5. Content Performance
# ---------------------------------------------------------------------------

class TestContentPerformance:
    """Tests for arclane.services.content_analytics."""

    async def test_record_and_get_performance(self, session, business):
        """Should record and retrieve content performance metrics."""
        from arclane.services.content_analytics import record_performance, get_content_performance

        content = Content(
            business_id=business.id,
            content_type="blog",
            title="Test Post",
            body="Test body",
            status="published",
        )
        session.add(content)
        await session.commit()
        await session.refresh(content)

        await record_performance(content.id, session, metric_name="views", value=150, source="manual")
        await record_performance(content.id, session, metric_name="clicks", value=25, source="manual")
        await session.commit()

        perf = await get_content_performance(content.id, session)
        assert perf["content_id"] == content.id
        assert "views" in perf["metrics"]
        assert perf["metrics"]["views"]["latest_value"] == 150

    async def test_content_insights_no_data(self, session, business):
        """Content insights should handle empty state gracefully."""
        from arclane.services.content_analytics import get_content_insights

        insights = await get_content_insights(business, session)
        assert len(insights) >= 1
        assert "No performance data" in insights[0]["insight"]


# ---------------------------------------------------------------------------
# 6. Route existence checks
# ---------------------------------------------------------------------------

class TestRouteImports:
    """Verify all new route modules are importable."""

    def test_forecast_route_importable(self):
        from arclane.api.routes.forecast import router

        assert router is not None

    def test_repurpose_route_importable(self):
        from arclane.api.routes.repurpose import router

        assert router is not None

    def test_benchmarks_route_importable(self):
        from arclane.api.routes.benchmarks import router

        assert router is not None

    def test_content_analytics_route_importable(self):
        from arclane.api.routes.content_analytics import router

        assert router is not None
