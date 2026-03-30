"""Test orchestrator."""

from arclane.engine.operating_plan import build_operating_plan, enqueue_add_on
from arclane.engine.orchestrator import ArclaneOrchestrator


def test_friendly_action_known_agents():
    orch = ArclaneOrchestrator()
    assert orch.friendly_action("cmo") == "Creating content"
    assert orch.friendly_action("cto") == "Building features"
    assert orch.friendly_action("cso") == "Researching market"
    assert orch.friendly_action("CSecO") == "Scanning for vulnerabilities"


def test_friendly_action_unknown_agent():
    orch = ArclaneOrchestrator()
    assert orch.friendly_action("unknown") == "Working on your business"


def test_friendly_action_case_insensitive():
    orch = ArclaneOrchestrator()
    assert orch.friendly_action("CMO") == "Creating content"
    assert orch.friendly_action("Cto") == "Building features"


def test_friendly_action_areas():
    orch = ArclaneOrchestrator()
    assert orch.friendly_action("strategy") == "Analyzing strategy"
    assert orch.friendly_action("market_research") == "Researching market"
    assert orch.friendly_action("content") == "Creating content"
    assert orch.friendly_action("operations") == "Setting up operations"
    assert orch.friendly_action("general") == "Working on your business"


class TestBuildTasks:
    """Test AIL workflow integration in task building."""

    def _make_business(self, template="content-site", description="Test biz"):
        """Create a mock business object."""
        class MockBusiness:
            pass
        b = MockBusiness()
        b.name = "Test Biz"
        b.template = template
        b.description = description
        b.slug = "test-biz"
        b.website_summary = None
        b.website_url = None
        b.agent_config = None
        return b

    def test_selects_one_queued_task_from_operating_plan(self):
        orch = ArclaneOrchestrator()
        biz = self._make_business(description="Dog grooming marketplace")
        biz.agent_config = {
            "operating_plan": build_operating_plan(
                name="Dog Lane",
                slug="dog-lane",
                description=biz.description,
                template="content-site",
            )
        }

        tasks = orch._build_tasks(biz)

        assert len(tasks) == 1
        assert tasks[0]["queue_task_key"] == "core-strategy-01"
        assert tasks[0]["night_index"] == 1
        assert tasks[0]["is_final_pass"] is True

    def test_add_on_cuts_ahead_of_pending_core_work(self):
        orch = ArclaneOrchestrator()
        biz = self._make_business(description="Automation agency")
        plan = build_operating_plan(
            name="Loopstack",
            slug="loopstack",
            description=biz.description,
            template="content-site",
        )
        plan["agent_tasks"][0]["queue_status"] = "completed"
        plan["add_on_offers"][0]["status"] = "available"
        plan = enqueue_add_on(plan, "deep-market-dive")
        biz.agent_config = {"operating_plan": plan}

        tasks = orch._build_tasks(biz)

        assert len(tasks) == 1
        assert tasks[0]["queue_task_key"] == "addon-market-01"
        assert tasks[0]["duration_days"] == 3

    def test_uses_ail_workflow_for_content_site(self):
        orch = ArclaneOrchestrator()
        if not orch._workflow_service.ail_available:
            return  # skip if ail not installed
        biz = self._make_business(template="content-site")
        tasks = orch._build_tasks(biz)
        assert len(tasks) >= 3  # content-site has PAR with 3+ branches

    def test_uses_ail_workflow_for_saas(self):
        orch = ArclaneOrchestrator()
        if not orch._workflow_service.ail_available:
            return
        biz = self._make_business(template="saas-app")
        tasks = orch._build_tasks(biz)
        assert len(tasks) >= 3

    def test_falls_back_to_static_plan(self):
        orch = ArclaneOrchestrator()
        # Force optimizer unavailable
        orch._workflow_service._optimizer_ok = False
        biz = self._make_business()
        tasks = orch._build_tasks(biz)
        assert len(tasks) == 4  # static plan always has 4 tasks
        assert tasks[0]["area"] == "strategy"

    def test_tasks_have_required_fields(self):
        orch = ArclaneOrchestrator()
        if not orch._workflow_service.ail_available:
            return
        biz = self._make_business(template="landing-page")
        tasks = orch._build_tasks(biz)
        for t in tasks:
            assert "area" in t
            assert "action" in t
            assert "description" in t

    def test_includes_business_description(self):
        orch = ArclaneOrchestrator()
        if not orch._workflow_service.ail_available:
            return
        biz = self._make_business(description="Dog grooming marketplace")
        tasks = orch._build_tasks(biz)
        descriptions = " ".join(t["description"] for t in tasks)
        assert "Dog grooming marketplace" in descriptions
