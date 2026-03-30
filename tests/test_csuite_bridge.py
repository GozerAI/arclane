"""C-Suite bridge — cycle requests, timeout, error handling, result processing."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.engine.orchestrator import ArclaneOrchestrator, AGENT_ACTION_MAP
from arclane.models.tables import Activity, Base, Business, Content, Cycle


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _create_biz_and_cycle(factory, slug="test-biz", plan_data=None):
    """Create a business and pending cycle, returning (business, cycle) objects."""
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.replace("-", " ").title(),
            description="A test business for automated tasks",
            owner_email="test@test.com",
            plan="starter",
            working_days_remaining=5,
            working_days_bonus=0,
            template="content-site",
        )
        session.add(biz)
        await session.commit()
        await session.refresh(biz)

        cycle = Cycle(
            business_id=biz.id,
            trigger="on_demand",
            status="pending",
            plan=plan_data,
        )
        session.add(cycle)
        await session.commit()
        await session.refresh(cycle)
        return biz.id, cycle.id


def _mock_integrations(orch):
    """Mock all integration clients on an orchestrator instance."""
    orch._ts_client = MagicMock()
    orch._ts_client.get_relevant_signals = AsyncMock(return_value=[])
    orch._kh_publisher = MagicMock()
    orch._kh_publisher.publish_cycle_results = AsyncMock(return_value=[])
    orch._nexus_publisher = MagicMock()
    orch._nexus_publisher.get_relevant_knowledge = AsyncMock(return_value=[])
    orch._nexus_publisher.publish_cycle_insights = AsyncMock(return_value=[])


# --- Cycle request to C-Suite ---


async def test_execute_cycle_sends_request_to_csuite(db):
    """Execute cycle sends POST to C-Suite bridge endpoint."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"area": "strategy", "status": "completed", "result": "Analysis done"},
        ],
        "failed": 0,
        "total": 1,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client), \
         patch("arclane.engine.orchestrator.send_cycle_complete_email", new_callable=AsyncMock), \
         patch("arclane.engine.orchestrator.send_working_days_low_email", new_callable=AsyncMock):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            result = await orch.execute_cycle(biz, cycle, session)

    assert result["total"] == 1
    assert result["failed"] == 0

    # Verify the POST was made to the C-Suite URL
    call_args = mock_client.post.call_args
    assert "/api/v1/arclane/cycle" in call_args.args[0]


async def test_cycle_sends_business_metadata(db):
    """Cycle request includes business ID, slug, name, description."""
    biz_id, cycle_id = await _create_biz_and_cycle(db, slug="meta-biz")

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [], "failed": 0, "total": 0}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client), \
         patch("arclane.engine.orchestrator.send_cycle_complete_email", new_callable=AsyncMock), \
         patch("arclane.engine.orchestrator.send_working_days_low_email", new_callable=AsyncMock):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["business_slug"] == "meta-biz"
    assert "business_name" in payload
    assert "business_description" in payload
    assert "tasks" in payload


async def test_cycle_sends_auth_headers(db):
    """Cycle request includes Authorization and X-Service-Token headers."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [], "failed": 0, "total": 0}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client), \
         patch("arclane.engine.orchestrator.send_cycle_complete_email", new_callable=AsyncMock), \
         patch("arclane.engine.orchestrator.send_working_days_low_email", new_callable=AsyncMock):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    headers = mock_client.post.call_args.kwargs["headers"]
    assert "Authorization" in headers
    assert "X-Service-Token" in headers


# --- C-Suite timeout handling ---


async def test_cycle_handles_request_timeout(db):
    """Cycle handles C-Suite timeout gracefully."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("Connection timed out"))

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            result = await orch.execute_cycle(biz, cycle, session)

    assert result["status"] == "failed"

    # Verify cycle status is set to failed
    async with db() as session:
        cycle = await session.get(Cycle, cycle_id)
        assert cycle.status == "failed"


async def test_cycle_handles_connection_error(db):
    """Cycle handles C-Suite connection refused."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            result = await orch.execute_cycle(biz, cycle, session)

    assert result["status"] == "failed"


# --- C-Suite error response handling ---


async def test_cycle_handles_500_from_csuite(db):
    """Cycle handles HTTP 500 from C-Suite."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=mock_resp
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            result = await orch.execute_cycle(biz, cycle, session)

    assert result["status"] == "failed"


async def test_failed_cycle_records_activity(db):
    """Failed cycle creates a 'Cycle failed' Activity record."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.RequestError("timeout"))

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    async with db() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Activity).where(Activity.business_id == biz_id)
        )
        activities = result.scalars().all()
        actions = [a.action for a in activities]
        assert "Cycle started" in actions
        assert "Cycle failed" in actions


# --- Cycle result processing ---


async def test_successful_cycle_creates_activities(db):
    """Successful cycle creates Activity records for each task result."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"area": "cmo", "status": "completed", "result": "Content created"},
            {"area": "cso", "status": "completed", "result": "Market analyzed"},
        ],
        "failed": 0,
        "total": 2,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    async with db() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Activity).where(Activity.business_id == biz_id)
        )
        activities = result.scalars().all()
        # Should have: Cycle started + 2 task results + Cycle completed = 4
        assert len(activities) >= 4


async def test_successful_cycle_stores_content(db):
    """Cycle stores Content records when task results include content."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {
                "area": "content",
                "status": "completed",
                "result": "Blog post created",
                "content_type": "blog",
                "content_title": "Getting Started",
                "content_body": "Welcome to our new business blog...",
            },
        ],
        "failed": 0,
        "total": 1,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    async with db() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Content).where(Content.business_id == biz_id)
        )
        contents = result.scalars().all()
        assert len(contents) == 1
        assert contents[0].content_type == "blog"
        assert contents[0].title == "Getting Started"
        assert contents[0].status == "draft"


async def test_cycle_marks_completed_on_success(db):
    """Successful cycle sets status to 'completed'."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [{"area": "general", "status": "completed", "result": "Done"}],
        "failed": 0,
        "total": 1,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    async with db() as session:
        cycle = await session.get(Cycle, cycle_id)
        assert cycle.status == "completed"
        assert cycle.completed_at is not None


async def test_cycle_all_failed_sets_status_failed(db):
    """Cycle with all tasks failed sets status to 'failed'."""
    biz_id, cycle_id = await _create_biz_and_cycle(db)

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [{"area": "general", "status": "failed", "result": "Error"}],
        "failed": 1,
        "total": 1,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    async with db() as session:
        cycle = await session.get(Cycle, cycle_id)
        assert cycle.status == "failed"


# --- Batch task execution ---


async def test_custom_task_description(db):
    """Cycle with task_description sends it as a single task."""
    biz_id, cycle_id = await _create_biz_and_cycle(
        db, plan_data={"task_description": "Write a blog post about AI"}
    )

    orch = ArclaneOrchestrator(execution_mode="bridge")
    _mock_integrations(orch)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [{"area": "general", "status": "completed", "result": "Blog written"}],
        "failed": 0,
        "total": 1,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("arclane.engine.orchestrator.httpx.AsyncClient", return_value=mock_client), \
         patch("arclane.engine.orchestrator.send_cycle_complete_email", new_callable=AsyncMock), \
         patch("arclane.engine.orchestrator.send_working_days_low_email", new_callable=AsyncMock):
        async with db() as session:
            biz = await session.get(Business, biz_id)
            cycle = await session.get(Cycle, cycle_id)
            await orch.execute_cycle(biz, cycle, session)

    payload = mock_client.post.call_args.kwargs["json"]
    tasks = payload["tasks"]
    # Custom task should be the main task
    assert any("Write a blog post about AI" in t["description"] for t in tasks)


# --- Friendly action mapping ---


def test_friendly_action_all_agents():
    """All AGENT_ACTION_MAP entries return non-generic labels."""
    orch = ArclaneOrchestrator()
    for agent in AGENT_ACTION_MAP:
        result = orch.friendly_action(agent)
        assert result != "Working on your business"
        assert isinstance(result, str)
        assert len(result) > 0


def test_friendly_action_unknown_returns_generic():
    """Unknown agent returns generic label."""
    orch = ArclaneOrchestrator()
    assert orch.friendly_action("unknown_agent") == "Working on your business"


def test_friendly_action_case_insensitive():
    """Action lookup is case insensitive."""
    orch = ArclaneOrchestrator()
    assert orch.friendly_action("CMO") == orch.friendly_action("cmo")
    assert orch.friendly_action("CTO") == orch.friendly_action("cto")


# --- Build tasks ---


def test_build_tasks_static_fallback():
    """When workflow service unavailable, static plan has 4 tasks."""
    orch = ArclaneOrchestrator()
    orch._workflow_service._optimizer_ok = False

    class MockBusiness:
        template = "content-site"
        description = "Test business"
        slug = "test"
        agent_config = None

    tasks = orch._build_tasks(MockBusiness())
    assert len(tasks) == 4
    assert tasks[0]["area"] == "strategy"
    assert tasks[1]["area"] == "market_research"
    assert tasks[2]["area"] == "content"
    assert tasks[3]["area"] == "operations"
