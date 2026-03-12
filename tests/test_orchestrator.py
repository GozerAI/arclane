"""Test orchestrator."""

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
        b.template = template
        b.description = description
        b.slug = "test-biz"
        return b

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
