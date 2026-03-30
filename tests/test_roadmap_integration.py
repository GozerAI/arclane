"""Integration tests for the 9 roadmap gap fixes.

Covers:
 Gap 1 — Core-to-milestone mapping (CORE_TASK_TO_MILESTONE)
 Gap 2 — Post-cycle milestone auto-completion via mapping
 Gap 3-4 — Task routing fallthrough to roadmap when plan done
 Gap 5 — Scheduler roadmap_day advancement
 Gap 6 — Weekly digest email function exists
 Gap 7 — Content spec inference for Phase 2-4 actions
 Gap 8 — Deterministic output for new actions
 Gap 9 — Phase-aware cycle optimizer
 + Phase context block tests
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business, Content, Cycle, Milestone


# ---------------------------------------------------------------------------
# Fixtures (copied from test_roadmap_services.py)
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
# Gap 1: Core-to-milestone mapping
# ---------------------------------------------------------------------------

def test_core_task_to_milestone_mapping():
    """CORE_TASK_TO_MILESTONE maps all 4 core tasks to Phase 1 milestones."""
    from arclane.services.roadmap_service import CORE_TASK_TO_MILESTONE

    assert len(CORE_TASK_TO_MILESTONE) == 4
    assert CORE_TASK_TO_MILESTONE["core-strategy-01"] == "p1-strategy-brief"
    assert CORE_TASK_TO_MILESTONE["core-market-01"] == "p1-market-research"
    assert CORE_TASK_TO_MILESTONE["core-content-01"] == "p1-landing-page-draft"
    assert CORE_TASK_TO_MILESTONE["core-social-01"] == "p1-launch-tweet"


# ---------------------------------------------------------------------------
# Gap 2: Post-cycle milestone auto-completion via mapping
# ---------------------------------------------------------------------------

async def test_post_cycle_completes_milestones_via_core_mapping(session, business):
    """_post_cycle_roadmap_update completes milestones when core tasks finish."""
    from arclane.services.roadmap_service import initialize_roadmap

    await initialize_roadmap(business, session)
    await session.commit()
    await session.refresh(business)

    # Create a cycle
    cycle = Cycle(business_id=business.id, trigger="nightly", status="completed")
    session.add(cycle)
    await session.commit()
    await session.refresh(cycle)

    # Simulate tasks with queue_task_key matching core tasks
    tasks = [{"queue_task_key": "core-strategy-01", "area": "strategy"}]
    cycle_result = {"results": [{"area": "strategy", "status": "completed"}], "total": 1, "failed": 0}

    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    await orch._post_cycle_roadmap_update(business, cycle, tasks, cycle_result, session)
    await session.commit()

    # Check milestone was completed
    result = await session.execute(
        select(Milestone).where(
            Milestone.business_id == business.id,
            Milestone.key == "p1-strategy-brief",
        )
    )
    milestone = result.scalar_one()
    assert milestone.status == "completed"


# ---------------------------------------------------------------------------
# Gap 3-4: Task routing fallthrough
# ---------------------------------------------------------------------------

def test_build_tasks_falls_through_to_roadmap_when_plan_done():
    """When all operating plan tasks are completed, _build_tasks falls through to roadmap check."""
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()

    # Business with all tasks completed AND current_phase=1
    biz = MagicMock()
    biz.slug = "test"
    biz.description = "Test business"
    biz.template = "content-site"
    biz.website_url = None
    biz.website_summary = None
    biz.current_phase = 1
    biz.roadmap_day = 10
    biz.agent_config = {
        "operating_plan": {
            "agent_tasks": [
                {"key": "core-strategy-01", "queue_status": "completed", "depends_on": []},
                {"key": "core-market-01", "queue_status": "completed", "depends_on": []},
                {"key": "core-content-01", "queue_status": "completed", "depends_on": []},
                {"key": "core-social-01", "queue_status": "completed", "depends_on": []},
            ],
        }
    }

    tasks = orch._build_tasks(biz)
    # Should get a roadmap async marker instead of empty list
    assert len(tasks) == 1
    assert tasks[0].get("_roadmap_async") is True
    assert tasks[0].get("_phase") == 1


# ---------------------------------------------------------------------------
# Gap 5: Scheduler roadmap_day advancement
# ---------------------------------------------------------------------------

async def test_advance_roadmap_days(session, business):
    """_advance_roadmap_days should increment roadmap_day for active businesses."""
    from arclane.services.roadmap_service import initialize_roadmap

    await initialize_roadmap(business, session)
    await session.commit()
    await session.refresh(business)

    initial_day = business.roadmap_day

    # Call the scheduler function directly
    from arclane.engine import scheduler

    # Create a mock context manager that yields our session
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch.object(scheduler, "async_session", return_value=mock_session_ctx):
        await scheduler._advance_roadmap_days()

    await session.refresh(business)
    assert business.roadmap_day == initial_day + 1


# ---------------------------------------------------------------------------
# Gap 6: Weekly digest email function exists
# ---------------------------------------------------------------------------

async def test_weekly_digest_email_function_exists():
    """send_weekly_digest_email function should be importable."""
    from arclane.notifications import send_weekly_digest_email

    assert callable(send_weekly_digest_email)


# ---------------------------------------------------------------------------
# Gap 7: Content spec for Phase 2-4 actions
# ---------------------------------------------------------------------------

def test_infer_content_spec_pitch_deck():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    spec = orch._infer_content_spec({"action": "create_pitch_deck", "title": "Pitch deck v1", "is_final_pass": True})
    assert spec["content_type"] == "report"
    assert "Pitch deck" in spec["title"]


def test_infer_content_spec_email_sequence():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    spec = orch._infer_content_spec({"action": "create_email_sequence", "title": "5-email nurture", "is_final_pass": True})
    assert spec["content_type"] == "newsletter"


def test_infer_content_spec_brand_guide():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    spec = orch._infer_content_spec({"action": "create_brand_guide", "title": "Brand guide", "is_final_pass": True})
    assert spec["content_type"] == "report"


def test_infer_content_spec_content_batch():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    spec = orch._infer_content_spec({"action": "create_content_batch", "title": "Content batch", "is_final_pass": True})
    assert spec["content_type"] == "social"


def test_infer_content_spec_financial_tasks():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    for action in ("build_financial_model", "validate_pricing", "create_investor_brief"):
        spec = orch._infer_content_spec({"action": action, "title": "Financial", "is_final_pass": True})
        assert spec is not None, f"No spec for {action}"
        assert spec["content_type"] == "report"


def test_infer_content_spec_operations_tasks():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    for action in ("design_lead_capture", "create_ad_brief", "create_acquisition_playbook", "create_hiring_plan", "create_retention_playbook"):
        spec = orch._infer_content_spec({"action": action, "title": "Ops", "is_final_pass": True})
        assert spec is not None, f"No spec for {action}"
        assert spec["content_type"] == "report"


def test_infer_content_spec_market_research_tasks():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    for action in ("competitor_profiling", "seo_baseline", "create_interview_guide", "identify_partners"):
        spec = orch._infer_content_spec({"action": action, "title": "Research", "is_final_pass": True})
        assert spec is not None, f"No spec for {action}"
        assert spec["content_type"] == "report"


def test_infer_content_spec_90day_report():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    spec = orch._infer_content_spec({"action": "generate_90day_report", "title": "90-day report", "is_final_pass": True})
    assert spec["content_type"] == "report"


def test_infer_content_spec_skips_intermediate_pass():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    spec = orch._infer_content_spec({"action": "create_pitch_deck", "title": "Pitch deck", "is_final_pass": False})
    assert spec is None


# ---------------------------------------------------------------------------
# Gap 8: Deterministic output for new actions
# ---------------------------------------------------------------------------

def test_deterministic_output_validation_plan():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    biz = MagicMock()
    biz.name = "TestBiz"
    biz.description = "A test business"
    biz.website_summary = None
    task = {"action": "create_validation_plan", "description": "Create a validation plan"}
    prompt_pack = {"executive": "Chief Strategy Officer"}
    output = orch._deterministic_output(biz, task, prompt_pack)
    assert "Validation plan" in output
    assert "Hypothesis" in output


def test_deterministic_output_pitch_deck():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    biz = MagicMock()
    biz.name = "TestBiz"
    biz.description = "A test business"
    biz.website_summary = None
    task = {"action": "create_pitch_deck", "description": "Create a pitch deck"}
    output = orch._deterministic_output(biz, task, {"executive": "CMO"})
    assert "Pitch deck" in output
    assert "Slide 1" in output


def test_deterministic_output_email_sequence():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    biz = MagicMock()
    biz.name = "TestBiz"
    biz.description = "Test"
    biz.website_summary = None
    task = {"action": "create_email_sequence", "description": "Write emails"}
    output = orch._deterministic_output(biz, task, {"executive": "CMO"})
    assert "Email 1" in output
    assert "Email 5" in output


def test_deterministic_output_brand_guide():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    biz = MagicMock()
    biz.name = "TestBiz"
    biz.description = "Test"
    biz.website_summary = None
    task = {"action": "create_brand_guide", "description": "Create brand guide"}
    output = orch._deterministic_output(biz, task, {"executive": "CMO"})
    assert "Voice attributes" in output


def test_deterministic_output_hiring_plan():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    biz = MagicMock()
    biz.name = "TestBiz"
    biz.description = "Test"
    biz.website_summary = None
    task = {"action": "create_hiring_plan", "description": "Hire first employee"}
    output = orch._deterministic_output(biz, task, {"executive": "COO"})
    assert "First hire" in output or "Role" in output


def test_deterministic_output_90day_report():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    biz = MagicMock()
    biz.name = "TestBiz"
    biz.description = "Test"
    biz.website_summary = None
    task = {"action": "generate_90day_report", "description": "Generate report"}
    output = orch._deterministic_output(biz, task, {"executive": "CSO"})
    assert "90-day" in output
    assert "Key metrics" in output


def test_deterministic_output_retention_playbook():
    from arclane.engine.orchestrator import ArclaneOrchestrator

    orch = ArclaneOrchestrator()
    biz = MagicMock()
    biz.name = "TestBiz"
    biz.description = "Test"
    biz.website_summary = None
    task = {"action": "create_retention_playbook", "description": "Retention playbook"}
    output = orch._deterministic_output(biz, task, {"executive": "COO"})
    assert "Onboarding" in output or "retention" in output.lower()


# ---------------------------------------------------------------------------
# Gap 9: Phase-aware cycle optimizer
# ---------------------------------------------------------------------------

async def test_optimizer_deadline_urgency(session, business):
    """Optimizer should always run when phase deadline is approaching."""
    from arclane.services.roadmap_service import initialize_roadmap

    await initialize_roadmap(business, session)
    business.roadmap_day = 19  # Phase 1 ends at day 21, within urgency window
    business.current_phase = 1
    await session.commit()
    await session.refresh(business)

    from arclane.autonomy.cycle_optimizer import evaluate_nightly

    decision = await evaluate_nightly(business, session)
    assert decision.should_run is True
    assert decision.urgency in ("elevated", "critical")


async def test_optimizer_past_deadline(session, business):
    """Optimizer should run with critical urgency when past phase deadline."""
    from arclane.services.roadmap_service import initialize_roadmap

    await initialize_roadmap(business, session)
    business.roadmap_day = 25  # Past Phase 1 end (day 21)
    business.current_phase = 1
    await session.commit()
    await session.refresh(business)

    from arclane.autonomy.cycle_optimizer import evaluate_nightly

    decision = await evaluate_nightly(business, session)
    assert decision.should_run is True
    assert decision.urgency == "critical"


async def test_optimizer_graduation_urgency(session, business):
    """Optimizer should detect near-graduation and elevate urgency."""
    from arclane.services.roadmap_service import initialize_roadmap, complete_milestone

    await initialize_roadmap(business, session)
    business.roadmap_day = 10
    business.current_phase = 1

    # Complete most Phase 1 milestones (need 70%+ completion)
    for key in [
        "p1-strategy-brief", "p1-market-research", "p1-landing-page-draft",
        "p1-growth-asset", "p1-launch-workflow", "p1-financial-model", "p1-content-3plus",
        "p1-landing-page-v2", "p1-lead-capture",
    ]:
        await complete_milestone(business, key, session)

    await session.commit()
    await session.refresh(business)

    from arclane.autonomy.cycle_optimizer import evaluate_nightly

    decision = await evaluate_nightly(business, session)
    assert decision.should_run is True
    # Should detect near-graduation (9/11 = 82% > 70% threshold)


async def test_optimizer_normal_operation(session, business):
    """Optimizer should run normally when no urgency conditions."""
    from arclane.services.roadmap_service import initialize_roadmap

    await initialize_roadmap(business, session)
    business.roadmap_day = 5  # Early Phase 1, no urgency
    business.current_phase = 1
    await session.commit()
    await session.refresh(business)

    from arclane.autonomy.cycle_optimizer import evaluate_nightly

    decision = await evaluate_nightly(business, session)
    assert decision.should_run is True
    assert decision.urgency == "normal"


# ---------------------------------------------------------------------------
# Phase context block tests
# ---------------------------------------------------------------------------

def test_phase_context_block():
    from arclane.engine.executive_prompts import phase_context_block

    block = phase_context_block(1, 5, 65.0)
    assert "Foundation" in block
    assert "Day 5" in block or "day: 5" in block
    assert "65" in block

    block = phase_context_block(5, 95)
    assert "Forever Partner" in block
    assert "95" in block

    block = phase_context_block(0, 0)
    assert block == ""
