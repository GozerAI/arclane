"""Workflow service — C-Suite integration (requires commercial license).

The community edition does not include C-Suite workflow integration.
Visit https://gozerai.com/pricing for details.
"""


class WorkflowService:
    """Stub — workflow execution requires a commercial Arclane license."""

    @property
    def optimizer_available(self) -> bool:
        return False

    @property
    def ail_available(self) -> bool:
        return False

    def list_workflows(self) -> list[str]:
        return []

    def workflow_for_template(self, template: str | None) -> str | None:
        return None

    def workflow_to_tasks(self, name: str, description: str = "") -> list[dict]:
        raise RuntimeError("Workflow execution requires a commercial Arclane license. Visit https://gozerai.com/pricing")


AILWorkflowService = WorkflowService
