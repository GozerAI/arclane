"""Tests for workflow service and API routes."""

import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from arclane.services.workflow_service import WorkflowService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workflows(tmp_path):
    """Create a temp workflows dir with sample .ail files."""
    (tmp_path / "default_cycle.ail").write_text(
        "@CSO ANALYZE strategy -> summary | @CEO DECIDE $prev"
    )
    (tmp_path / "content_site_cycle.ail").write_text(
        "PAR { @CMO ANALYZE audience; @CDO GATHER analytics }\n@CMO CREATE content"
    )
    (tmp_path / "bad_syntax.ail").write_text("THIS IS NOT VALID AIL @@@ !!!")
    return tmp_path


@pytest.fixture
def service(tmp_workflows):
    return WorkflowService(workflows_dir=tmp_workflows)


# ---------------------------------------------------------------------------
# WorkflowService
# ---------------------------------------------------------------------------

class TestListWorkflows:
    def test_lists_ail_files(self, service):
        names = service.list_workflows()
        assert "default_cycle" in names
        assert "content_site_cycle" in names

    def test_excludes_non_ail(self, tmp_workflows):
        (tmp_workflows / "readme.txt").write_text("not a workflow")
        svc = WorkflowService(workflows_dir=tmp_workflows)
        assert "readme" not in svc.list_workflows()

    def test_empty_dir(self, tmp_path):
        svc = WorkflowService(workflows_dir=tmp_path)
        assert svc.list_workflows() == []

    def test_missing_dir(self, tmp_path):
        svc = WorkflowService(workflows_dir=tmp_path / "nonexistent")
        assert svc.list_workflows() == []


class TestLoadWorkflow:
    def test_loads_file(self, service):
        source = service.load_workflow("default_cycle")
        assert "@CSO ANALYZE" in source
        assert "@CEO DECIDE" in source

    def test_missing_raises(self, service):
        with pytest.raises(FileNotFoundError, match="not_a_thing"):
            service.load_workflow("not_a_thing")


class TestValidateWorkflow:
    def test_valid_workflow(self, service):
        source = service.load_workflow("default_cycle")
        result = service.validate_workflow(source)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_invalid_syntax(self, service):
        result = service.validate_workflow("@@@ NOT VALID")
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_parallel_workflow(self, service):
        source = service.load_workflow("content_site_cycle")
        result = service.validate_workflow(source)
        assert result["valid"] is True


class TestDryRun:
    def test_dry_run_returns_steps(self, service):
        source = service.load_workflow("default_cycle")
        steps = service.dry_run(source)
        assert len(steps) >= 2
        directives = [s for s in steps if s.get("type") == "directive"]
        agents = [s["agent"] for s in directives]
        assert "CSO" in agents
        assert "CEO" in agents

    def test_dry_run_parallel(self, service):
        source = service.load_workflow("content_site_cycle")
        steps = service.dry_run(source)
        par_steps = [s for s in steps if s.get("type") == "parallel"]
        assert len(par_steps) >= 1


class TestExecuteWorkflow:
    @pytest.mark.asyncio
    async def test_execute_returns_result(self, service):
        result = await service.execute_workflow("default_cycle")
        assert result is not None

    @pytest.mark.asyncio
    async def test_execute_missing_raises(self, service):
        with pytest.raises(FileNotFoundError):
            await service.execute_workflow("nonexistent")


class TestWorkflowForTemplate:
    def test_content_site(self, service):
        name = service.workflow_for_template("content-site")
        assert name == "content_site_cycle"

    def test_unknown_template_defaults(self, service):
        name = service.workflow_for_template("unknown-template")
        assert name == "default_cycle"

    def test_none_template_defaults(self, service):
        name = service.workflow_for_template(None)
        assert name == "default_cycle"


class TestWorkflowToTasks:
    def test_converts_default_cycle(self, service):
        tasks = service.workflow_to_tasks("default_cycle")
        assert len(tasks) >= 2
        for t in tasks:
            assert "area" in t
            assert "action" in t
            assert "description" in t

    def test_includes_business_description(self, service):
        tasks = service.workflow_to_tasks("default_cycle", "Pet grooming SaaS")
        descriptions = " ".join(t["description"] for t in tasks)
        assert "Pet grooming SaaS" in descriptions

    def test_parallel_branches_become_flat_tasks(self, service):
        tasks = service.workflow_to_tasks("content_site_cycle")
        assert len(tasks) >= 3

    def test_maps_agents_to_areas(self, service):
        tasks = service.workflow_to_tasks("default_cycle")
        areas = {t["area"] for t in tasks}
        assert areas != {"general"}

    def test_missing_workflow_raises(self, service):
        with pytest.raises(FileNotFoundError):
            service.workflow_to_tasks("nonexistent")


class TestRealWorkflows:
    """Test the actual workflow files shipped in workflows/."""

    def test_all_shipped_workflows_valid(self):
        svc = WorkflowService()
        for name in svc.list_workflows():
            source = svc.load_workflow(name)
            result = svc.validate_workflow(source)
            assert result["valid"], f"{name}.ail invalid: {result['errors']}"

    def test_shipped_workflows_to_tasks(self):
        svc = WorkflowService()
        for name in svc.list_workflows():
            tasks = svc.workflow_to_tasks(name, "test business")
            assert len(tasks) > 0, f"{name}.ail produced no tasks"
            for t in tasks:
                assert t.get("area"), f"{name}.ail task missing area"
                assert t.get("description"), f"{name}.ail task missing description"

    def test_shipped_workflows_dry_run(self):
        svc = WorkflowService()
        for name in svc.list_workflows():
            source = svc.load_workflow(name)
            steps = svc.dry_run(source)
            assert len(steps) > 0, f"{name}.ail produced no steps"


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

class TestWorkflowAPI:
    @pytest.fixture
    def client(self, tmp_workflows, monkeypatch):
        """Create test client with patched workflows dir."""
        from arclane.api.routes import workflows as wf_mod
        monkeypatch.setattr(wf_mod, "_service", WorkflowService(workflows_dir=tmp_workflows))

        from arclane.api.app import app
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    @pytest.mark.asyncio
    async def test_list(self, client):
        async with client as c:
            resp = await c.get("/api/workflows/")
            assert resp.status_code == 200
            data = resp.json()
            assert "default_cycle" in data["workflows"]

    @pytest.mark.asyncio
    async def test_get_workflow(self, client):
        async with client as c:
            resp = await c.get("/api/workflows/default_cycle")
            assert resp.status_code == 200
            assert "@CSO ANALYZE" in resp.json()["source"]

    @pytest.mark.asyncio
    async def test_get_missing(self, client):
        async with client as c:
            resp = await c.get("/api/workflows/nonexistent")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_validate(self, client):
        async with client as c:
            resp = await c.post("/api/workflows/default_cycle/validate")
            assert resp.status_code == 200
            assert resp.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_dry_run(self, client):
        async with client as c:
            resp = await c.post("/api/workflows/default_cycle/dry-run")
            assert resp.status_code == 200
            steps = resp.json()["steps"]
            assert len(steps) >= 2

    @pytest.mark.asyncio
    async def test_dry_run_missing(self, client):
        async with client as c:
            resp = await c.post("/api/workflows/nonexistent/dry-run")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_tasks_endpoint(self, client):
        async with client as c:
            resp = await c.post("/api/workflows/default_cycle/tasks")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] >= 2
            assert len(data["tasks"]) == data["count"]

    @pytest.mark.asyncio
    async def test_tasks_missing(self, client):
        async with client as c:
            resp = await c.post("/api/workflows/nonexistent/tasks")
            assert resp.status_code == 404
