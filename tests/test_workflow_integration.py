"""Cross-repo integration tests: workflow → task conversion → C-Suite payload.

Tests the full pipeline from .ail workflow files through to the task format
expected by C-Suite's POST /api/v1/arclane/cycle endpoint.
"""

import pytest

from arclane.engine.orchestrator import ArclaneOrchestrator
from arclane.services.workflow_service import WorkflowService


# ---------------------------------------------------------------------------
# Skip all if prompt-optimizer not installed
# ---------------------------------------------------------------------------

_svc = WorkflowService()
pytestmark = pytest.mark.skipif(
    not _svc.optimizer_available, reason="prompt-optimizer not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_business(template="content-site", description="Online pet grooming marketplace"):
    class B:
        pass
    b = B()
    b.template = template
    b.description = description
    b.slug = "pet-groom"
    return b


# C-Suite ArclaneTaskItem expected fields
REQUIRED_TASK_FIELDS = {"area", "action", "description"}

# C-Suite _ARCLANE_TASK_MAP keys (valid areas the bridge recognizes)
VALID_AREAS = {
    "strategy", "market_research", "content", "operations",
    "engineering", "security", "finance", "general",
}


# ---------------------------------------------------------------------------
# Workflow → Task format tests
# ---------------------------------------------------------------------------

class TestWorkflowTaskFormat:
    """Verify that workflow-generated tasks match C-Suite's expected format."""

    @pytest.fixture(params=["default_cycle", "content_site_cycle", "saas_app_cycle", "landing_page_cycle"])
    def workflow_tasks(self, request):
        svc = WorkflowService()
        return svc.workflow_to_tasks(request.param, "Test business description")

    def test_tasks_have_required_fields(self, workflow_tasks):
        for task in workflow_tasks:
            missing = REQUIRED_TASK_FIELDS - set(task.keys())
            assert not missing, f"Task missing fields: {missing}"

    def test_areas_are_valid(self, workflow_tasks):
        for task in workflow_tasks:
            assert task["area"] in VALID_AREAS, (
                f"Unknown area '{task['area']}' — C-Suite will default to general"
            )

    def test_descriptions_are_nonempty(self, workflow_tasks):
        for task in workflow_tasks:
            assert len(task["description"].strip()) > 0

    def test_actions_are_nonempty(self, workflow_tasks):
        for task in workflow_tasks:
            assert len(task["action"].strip()) > 0

    def test_produces_multiple_tasks(self, workflow_tasks):
        assert len(workflow_tasks) >= 2, "Workflow should produce at least 2 tasks"


# ---------------------------------------------------------------------------
# Template → Workflow mapping
# ---------------------------------------------------------------------------

class TestTemplateMapping:
    """Verify all Arclane templates map to real .ail files."""

    TEMPLATES = ["content-site", "saas-app", "landing-page"]

    def test_all_templates_have_workflows(self):
        svc = WorkflowService()
        for template in self.TEMPLATES:
            name = svc.workflow_for_template(template)
            assert name is not None, f"No workflow for template '{template}'"
            source = svc.load_workflow(name)
            assert len(source) > 0

    def test_unknown_template_falls_back(self):
        svc = WorkflowService()
        name = svc.workflow_for_template("unknown-thing")
        assert name == "default_cycle"

    def test_none_template_falls_back(self):
        svc = WorkflowService()
        name = svc.workflow_for_template(None)
        assert name == "default_cycle"


# ---------------------------------------------------------------------------
# Orchestrator end-to-end (no network calls)
# ---------------------------------------------------------------------------

class TestOrchestratorWorkflowIntegration:
    """Test that the orchestrator builds correct tasks from workflows."""

    def test_content_site_produces_content_tasks(self):
        orch = ArclaneOrchestrator()
        biz = _mock_business(template="content-site")
        tasks = orch._build_tasks(biz)
        areas = [t["area"] for t in tasks]
        assert "content" in areas, "Content-site workflow should produce content tasks"

    def test_saas_app_produces_engineering_tasks(self):
        orch = ArclaneOrchestrator()
        biz = _mock_business(template="saas-app")
        tasks = orch._build_tasks(biz)
        areas = [t["area"] for t in tasks]
        assert "engineering" in areas or "strategy" in areas, (
            "SaaS workflow should produce engineering or strategy tasks"
        )

    def test_landing_page_produces_content_tasks(self):
        orch = ArclaneOrchestrator()
        biz = _mock_business(template="landing-page")
        tasks = orch._build_tasks(biz)
        areas = [t["area"] for t in tasks]
        assert "content" in areas or "market_research" in areas

    def test_all_templates_produce_valid_payloads(self):
        """Simulate what would be sent to C-Suite for each template."""
        orch = ArclaneOrchestrator()
        for template in ["content-site", "saas-app", "landing-page", None]:
            biz = _mock_business(template=template)
            tasks = orch._build_tasks(biz)

            payload = {
                "business_id": 1,
                "business_slug": biz.slug,
                "business_name": "Test Biz",
                "business_description": biz.description,
                "cycle_id": 1,
                "tasks": tasks,
            }

            assert len(payload["tasks"]) >= 2
            for task in payload["tasks"]:
                assert REQUIRED_TASK_FIELDS <= set(task.keys())
                assert task["area"] in VALID_AREAS

    def test_business_description_propagates(self):
        orch = ArclaneOrchestrator()
        desc = "AI-powered dog grooming appointments"
        biz = _mock_business(description=desc)
        tasks = orch._build_tasks(biz)
        all_descriptions = " ".join(t["description"] for t in tasks)
        assert desc in all_descriptions


# ---------------------------------------------------------------------------
# Parse → validate → dry-run → task roundtrip
# ---------------------------------------------------------------------------

class TestWorkflowRoundtrip:
    """Test that workflows survive parse → validate → dry-run → task conversion."""

    def test_all_shipped_workflows_roundtrip(self):
        svc = WorkflowService()
        for name in svc.list_workflows():
            # Parse and validate
            source = svc.load_workflow(name)
            validation = svc.validate_workflow(source)
            assert validation["valid"], f"{name}: {validation['errors']}"

            # Dry-run
            steps = svc.dry_run(source)
            assert len(steps) > 0, f"{name} produced no steps"

            # Convert to tasks
            tasks = svc.workflow_to_tasks(name, "roundtrip test")
            assert len(tasks) > 0, f"{name} produced no tasks"

            # Validate task format
            for task in tasks:
                assert REQUIRED_TASK_FIELDS <= set(task.keys()), (
                    f"{name} task missing fields: {REQUIRED_TASK_FIELDS - set(task.keys())}"
                )

    def test_workflow_emits_valid_output(self):
        """Parse → render → re-parse roundtrip."""
        from prompt_optimizer.grammar import Lexer, Parser, Renderer

        svc = WorkflowService()
        renderer = Renderer()
        for name in svc.list_workflows():
            source = svc.load_workflow(name)
            tokens = Lexer().tokenize(source)
            node = Parser(tokens).parse()
            emitted = renderer.render(node)
            # Re-parse the emitted version
            tokens2 = Lexer().tokenize(emitted)
            Parser(tokens2).parse()
            # Both should produce the same dry-run output
            steps1 = svc.dry_run(source)
            steps2 = svc.dry_run(emitted)
            assert len(steps1) == len(steps2), (
                f"{name}: roundtrip changed step count ({len(steps1)} → {len(steps2)})"
            )
