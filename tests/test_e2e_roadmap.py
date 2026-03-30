"""End-to-end integration tests for the 90-day incubator lifecycle."""

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
        slug="e2e-test",
        name="E2E Test Business",
        description="An e2e test for the full incubator lifecycle",
        owner_email="e2e@example.com",
        plan="pro",
        working_days_remaining=20,
    )
    session.add(biz)
    await session.commit()
    await session.refresh(biz)
    return biz


class TestFullLifecycle:
    async def test_roadmap_initialization(self, session, business):
        """Business creation should initialize roadmap with 4 phases and milestones."""
        from arclane.services.roadmap_service import initialize_roadmap, get_roadmap_summary

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        assert business.roadmap_day == 1
        assert business.current_phase == 1

        summary = await get_roadmap_summary(business, session)
        assert len(summary["phases"]) == 4
        assert summary["phases"][0]["status"] == "active"
        assert summary["phases"][1]["status"] == "locked"

        # Count total milestones
        total = sum(p["milestones_total"] for p in summary["phases"])
        assert total >= 30  # Should have 30+ milestones across all phases

    async def test_milestone_completion_and_graduation_check(self, session, business):
        """Completing milestones should update graduation readiness."""
        from arclane.services.roadmap_service import (
            initialize_roadmap,
            complete_milestone,
            check_phase_graduation,
        )

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        # Not ready before completing milestones
        check = await check_phase_graduation(business, session)
        assert not check["ready"]

        # Complete required milestones (Day 1 pack + ICP + lead capture + financial model + graduation check)
        for key in [
            "p1-strategy-brief",
            "p1-market-research",
            "p1-landing-page-draft",
            "p1-icp-document",
            "p1-lead-capture",
            "p1-financial-model",
            "p1-graduation-check",
        ]:
            await complete_milestone(business, key, session)

        # Add content to meet the content_minimum (5)
        for i in range(5):
            session.add(
                Content(
                    business_id=business.id,
                    content_type="blog",
                    title=f"Blog {i}",
                    body=f"Content {i}",
                    status="published",
                )
            )
        await session.commit()

        check = await check_phase_graduation(business, session)
        assert check["ready"]
        assert check["score"] == 100.0

    async def test_phase_advancement(self, session, business):
        """Should advance from Phase 1 to Phase 2 when graduation criteria met."""
        from arclane.services.roadmap_service import (
            initialize_roadmap,
            complete_milestone,
            advance_phase,
            get_roadmap_summary,
        )

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        # Complete Phase 1 required milestones
        for key in [
            "p1-strategy-brief",
            "p1-market-research",
            "p1-landing-page-draft",
            "p1-icp-document",
            "p1-lead-capture",
            "p1-financial-model",
            "p1-graduation-check",
        ]:
            await complete_milestone(business, key, session)
        for i in range(5):
            session.add(
                Content(
                    business_id=business.id,
                    content_type="blog",
                    title=f"Blog {i}",
                    body=f"Content {i}",
                    status="published",
                )
            )
        await session.commit()

        result = await advance_phase(business, session)
        await session.commit()
        await session.refresh(business)

        assert result["advanced"]
        assert result["from_phase"] == 1
        assert result["to_phase"] == 2
        assert business.current_phase == 2

        summary = await get_roadmap_summary(business, session)
        assert summary["phases"][0]["status"] == "completed"
        assert summary["phases"][1]["status"] == "active"

    async def test_health_score_after_cycle(self, session, business):
        """Health score should be calculated based on business state."""
        from arclane.services.roadmap_service import initialize_roadmap
        from arclane.services.health_score_service import (
            calculate_health_score,
            record_health_snapshot,
        )

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        score = await calculate_health_score(business, session)
        assert "overall" in score
        assert "sub_scores" in score
        assert all(
            k in score["sub_scores"]
            for k in ["market_fit", "content", "revenue", "operations", "momentum"]
        )

        # Record snapshot
        overall = await record_health_snapshot(business, session)
        await session.commit()
        assert overall >= 0
        assert business.health_score is not None

    async def test_advisory_notes_generation(self, session, business):
        """Advisory notes should be generated based on business state."""
        from arclane.services.roadmap_service import initialize_roadmap
        from arclane.services.advisory_service import generate_advisory_notes

        await initialize_roadmap(business, session)
        business.working_days_remaining = 2  # Trigger low working days warning
        await session.commit()
        await session.refresh(business)

        notes = await generate_advisory_notes(business, session)
        assert len(notes) > 0
        categories = [n["category"] for n in notes]
        assert "warning" in categories  # Low working days

    async def test_forecast_computation(self, session, business):
        """Forecast should return meaningful data for an active business."""
        from arclane.services.roadmap_service import initialize_roadmap, complete_milestone
        from arclane.services.roadmap_forecaster import compute_forecast

        await initialize_roadmap(business, session)
        business.roadmap_day = 10
        await complete_milestone(business, "p1-strategy-brief", session)
        await session.commit()
        await session.refresh(business)

        forecast = await compute_forecast(business, session)
        assert forecast["velocity"]["completed"] == 1
        assert forecast["pace"]["status"] in (
            "ahead",
            "on_track",
            "slightly_behind",
            "significantly_behind",
        )
        assert "action" in forecast["weekly_focus"]

    async def test_content_repurposing_creates_new_content(self, session, business):
        """Repurposing content should create a new draft content item."""
        content = Content(
            business_id=business.id,
            content_type="blog",
            title="Original Blog Post",
            body=(
                "This is a long blog post about building a startup. "
                "It covers many important topics about business growth and market positioning. "
                "The key insight is that clarity beats complexity."
            ),
            status="published",
        )
        session.add(content)
        await session.commit()
        await session.refresh(content)

        from arclane.services.content_repurposer import repurpose

        result = repurpose("blog", content.title, content.body, "twitter_thread")

        assert result["format"] == "twitter_thread"
        assert len(result["pieces"]) >= 2

    async def test_benchmarks_with_multiple_businesses(self, session, business):
        """Benchmarks should compare against cohort when multiple businesses exist."""
        from arclane.services.roadmap_service import initialize_roadmap
        from arclane.services.benchmarks import compute_benchmarks

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        # Create a second business for cohort comparison
        biz2 = Business(
            slug="cohort-biz",
            name="Cohort Business",
            description="A cohort comparison business",
            owner_email="cohort@example.com",
            plan="pro",
            working_days_remaining=20,
            roadmap_day=15,
            current_phase=1,
        )
        session.add(biz2)
        await session.commit()
        await session.refresh(biz2)

        # Add some content for biz2
        for i in range(5):
            session.add(
                Content(
                    business_id=biz2.id,
                    content_type="blog",
                    title=f"Cohort blog {i}",
                    body=f"Content {i}",
                    status="published",
                )
            )
        await session.commit()

        benchmarks = await compute_benchmarks(business, session)
        assert "metrics" in benchmarks
        assert benchmarks["metrics"]["content_total"]["cohort_size"] >= 1

    async def test_full_phase1_to_phase2_with_health_and_advisory(self, session, business):
        """Full lifecycle: init -> milestones -> health -> advisory -> advance."""
        from arclane.services.roadmap_service import (
            initialize_roadmap,
            complete_milestone,
            advance_phase,
            get_next_actions,
        )
        from arclane.services.health_score_service import (
            calculate_health_score,
            record_health_snapshot,
        )
        from arclane.services.advisory_service import generate_advisory_notes

        # Step 1: Initialize
        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)
        assert business.current_phase == 1

        # Step 2: Check next actions
        actions = await get_next_actions(business, session)
        assert len(actions) > 0

        # Step 3: Record initial health
        initial_score = await record_health_snapshot(business, session)
        await session.commit()
        assert initial_score >= 0

        # Step 4: Complete milestones
        for key in [
            "p1-strategy-brief",
            "p1-market-research",
            "p1-landing-page-draft",
            "p1-icp-document",
            "p1-lead-capture",
            "p1-financial-model",
            "p1-graduation-check",
        ]:
            await complete_milestone(business, key, session)
        for i in range(5):
            session.add(
                Content(
                    business_id=business.id,
                    content_type="blog",
                    title=f"Blog {i}",
                    body=f"Content {i}",
                    status="published",
                )
            )
        await session.commit()

        # Step 5: Check advisory notes (no low-working-day warnings with 20 working days)
        notes = await generate_advisory_notes(business, session)
        wd_warnings = [n for n in notes if "Working days running low" in n.get("title", "")]
        assert len(wd_warnings) == 0

        # Step 6: Health should improve after milestones
        post_score = await calculate_health_score(business, session)
        assert post_score["overall"] >= initial_score

        # Step 7: Advance
        result = await advance_phase(business, session)
        await session.commit()
        await session.refresh(business)
        assert result["advanced"]
        assert business.current_phase == 2

    async def test_forecast_with_no_milestones_completed(self, session, business):
        """Forecast should handle the case where no milestones are completed."""
        from arclane.services.roadmap_service import initialize_roadmap
        from arclane.services.roadmap_forecaster import compute_forecast

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        forecast = await compute_forecast(business, session)
        assert forecast["velocity"]["completed"] == 0
        assert forecast["velocity"]["milestones_per_week"] == 0
        assert forecast["graduation_eta"]["status"] == "insufficient_data"
        assert forecast["streak"]["current"] == 0

    async def test_advance_phase_not_ready_returns_not_advanced(self, session, business):
        """advance_phase should not advance when criteria are not met."""
        from arclane.services.roadmap_service import initialize_roadmap, advance_phase

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        result = await advance_phase(business, session)
        assert result["advanced"] is False
        assert business.current_phase == 1

    async def test_repurpose_multiple_formats(self, session, business):
        """Test repurposing into multiple formats from the same source."""
        from arclane.services.content_repurposer import repurpose, available_formats

        title = "Building a Startup"
        body = (
            "Starting a business requires careful planning and execution. "
            "You need to understand your market and build something people want. "
            "The most successful founders focus on solving real problems. "
            "Distribution is just as important as the product itself."
        )

        # Check available formats for blog
        formats = available_formats("blog")
        assert len(formats) >= 3
        format_names = {f["format"] for f in formats}
        assert "twitter_thread" in format_names
        assert "linkedin_carousel" in format_names

        # Repurpose to each available format
        for fmt in formats:
            result = repurpose("blog", title, body, fmt["format"])
            assert "error" not in result
            assert result["format"] == fmt["format"]
            assert len(result["pieces"]) >= 1

    async def test_health_trend_after_multiple_snapshots(self, session, business):
        """Health trend should accumulate snapshots over time."""
        from arclane.services.roadmap_service import initialize_roadmap
        from arclane.services.health_score_service import record_health_snapshot, get_health_trend

        await initialize_roadmap(business, session)
        await session.commit()
        await session.refresh(business)

        # Record 3 snapshots
        for _ in range(3):
            await record_health_snapshot(business, session)
            await session.commit()

        trend = await get_health_trend(business, session, days=30)
        assert len(trend) == 3
        assert all("score" in entry for entry in trend)
        assert all("recorded_at" in entry for entry in trend)
