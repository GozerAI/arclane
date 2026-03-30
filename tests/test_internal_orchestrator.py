"""Tests for Arclane's internal prompt-driven orchestrator mode."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.engine.operating_plan import build_operating_plan
from arclane.engine.orchestrator import ArclaneOrchestrator
from arclane.models.tables import Base, Business, Content, Cycle, Metric


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_internal_cycle_creates_content_and_completes(db):
    async with db() as session:
        business = Business(
            slug="internal-biz",
            name="Internal Biz",
            description="A consulting business that wants better homepage conversion",
            website_url="https://example.com",
            website_summary="Title: Example Consulting. Key headings: Fractional COO support; Case studies.",
            owner_email="owner@example.com",
            template="content-site",
        )
        cycle = Cycle(trigger="on_demand", status="pending")
        business.cycles.append(cycle)
        session.add(business)
        await session.commit()
        await session.refresh(business)
        await session.refresh(cycle)

        orch = ArclaneOrchestrator(execution_mode="internal")
        orch._workflow_service._optimizer_ok = False
        orch._kh_publisher = MagicMock()
        orch._kh_publisher.publish_cycle_results = AsyncMock(return_value=[])

        with patch("arclane.engine.orchestrator.send_cycle_complete_email", new_callable=AsyncMock), \
             patch("arclane.engine.orchestrator.send_working_days_low_email", new_callable=AsyncMock):
            result = await orch.execute_cycle(business, cycle, session)

        assert result["mode"] == "internal"
        assert result["total"] == 4
        assert result["failed"] == 0

    async with db() as session:
        stored_cycle = await session.get(Cycle, cycle.id)
        assert stored_cycle.status == "completed"

        content_result = await session.execute(select(Content).where(Content.business_id == business.id))
        contents = content_result.scalars().all()
        assert contents
        assert any(item.content_type == "report" and item.title == "Market research report" for item in contents)
        assert any(item.content_type == "report" and item.title == "Mission and positioning brief" for item in contents)
        assert any(item.content_type in {"social", "blog"} for item in contents)

        metric_result = await session.execute(select(Metric).where(Metric.business_id == business.id))
        metrics = metric_result.scalars().all()
        assert metrics
        metric_names = {item.name for item in metrics}
        assert "tasks_completed" in metric_names
        assert "content_total" in metric_names
        assert "cycles_completed" in metric_names


async def test_operating_plan_completes_tasks_and_unlocks_addons(db):
    """All Day 1 tasks complete in a single cycle and unlock add-on offers."""
    async with db() as session:
        business = Business(
            slug="queue-biz",
            name="Queue Biz",
            description="An automation service for local contractors",
            owner_email="owner@example.com",
            template="content-site",
            agent_config={
                "operating_plan": build_operating_plan(
                    name="Queue Biz",
                    slug="queue-biz",
                    description="An automation service for local contractors",
                    template="content-site",
                )
            },
        )
        cycle = Cycle(trigger="initial", status="pending")
        business.cycles.append(cycle)
        session.add(business)
        await session.commit()
        await session.refresh(business)
        await session.refresh(cycle)

        orch = ArclaneOrchestrator(execution_mode="internal")
        orch._workflow_service._optimizer_ok = False
        orch._kh_publisher = MagicMock()
        orch._kh_publisher.publish_cycle_results = AsyncMock(return_value=[])

        with patch("arclane.engine.orchestrator.send_task_complete_email", new_callable=AsyncMock) as mock_task_email:
            result = await orch.execute_cycle(business, cycle, session)

        # All 4 Day 1 tasks should complete in one cycle
        assert result["total"] == 4
        assert result["failed"] == 0
        for task_result in result["results"]:
            assert task_result["status"] == "completed"

        # Per-task emails should have been sent for each completed task
        assert mock_task_email.call_count == 4

    async with db() as session:
        stored_business = await session.scalar(select(Business).where(Business.slug == "queue-biz"))
        updated_plan = stored_business.agent_config["operating_plan"]

        # All core tasks should be completed
        for task in updated_plan["agent_tasks"]:
            assert task["queue_status"] == "completed"
            assert task["days_remaining"] == 0

        # Market research completion should unlock deep-market-dive add-on
        add_on_offer = next(item for item in updated_plan["add_on_offers"] if item["key"] == "deep-market-dive")
        assert add_on_offer["status"] == "available"

        # Content should have been produced
        content_result = await session.execute(select(Content).where(Content.business_id == stored_business.id))
        contents = content_result.scalars().all()
        assert any(item.title == "Market research report" for item in contents)
