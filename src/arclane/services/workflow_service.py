"""Workflow service — load and execute .ail workflow files.

Provides a service layer for managing workflow programs. Business cycles
can be defined as .ail files instead of hardcoded Python, making them
versionable, diffable, and composable.

Requires: pip install prompt-optimizer (optional dependency, graceful degradation)
"""

from pathlib import Path
from typing import Any

from arclane.core.logging import get_logger

log = get_logger("workflow_service")

_optimizer_available: bool | None = None


def _check_optimizer() -> bool:
    global _optimizer_available
    if _optimizer_available is None:
        try:
            import prompt_optimizer  # noqa: F401
            _optimizer_available = True
        except ImportError:
            _optimizer_available = False
    return _optimizer_available


# Default workflows directory (next to src/)
_DEFAULT_DIR = Path(__file__).parent.parent.parent.parent / "workflows"


# Map C-Suite agent codes to Arclane task areas
_AGENT_AREA_MAP = {
    "cso": "market_research",
    "cmo": "content",
    "cto": "engineering",
    "cdo": "market_research",
    "cfo": "finance",
    "cpo": "strategy",
    "cro": "finance",
    "ceo": "strategy",
    "coo": "operations",
    "cio": "engineering",
    "cco": "content",
}


class WorkflowService:
    """Load and execute .ail workflow files for business cycles."""

    def __init__(self, workflows_dir: Path | None = None):
        self._dir = workflows_dir or _DEFAULT_DIR
        self._optimizer_ok = _check_optimizer()

    @property
    def optimizer_available(self) -> bool:
        return self._optimizer_ok

    # Keep backward-compat property
    @property
    def ail_available(self) -> bool:
        return self._optimizer_ok

    @property
    def workflows_dir(self) -> Path:
        return self._dir

    def list_workflows(self) -> list[str]:
        """List available .ail workflow files (names without extension)."""
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.ail"))

    def load_workflow(self, name: str) -> str:
        """Load a .ail file by name (without extension).

        Raises:
            FileNotFoundError: If the workflow file doesn't exist.
        """
        path = (self._dir / f"{name}.ail").resolve()
        if not str(path).startswith(str(self._dir.resolve())):
            raise FileNotFoundError(f"Workflow not found: {name}")
        if not path.exists():
            raise FileNotFoundError(f"Workflow not found: {name}")
        return path.read_text(encoding="utf-8")

    def validate_workflow(self, source: str) -> dict:
        """Parse and validate a workflow program.

        Returns:
            Dict with keys: valid (bool), errors (list), warnings (list),
            ast_repr (str), and node_type (str).
        """
        if not self._optimizer_ok:
            return {
                "valid": False,
                "errors": ["prompt-optimizer not installed. Install with: pip install prompt-optimizer"],
                "warnings": [],
            }

        from prompt_optimizer.grammar import Lexer, Parser, Validator
        from prompt_optimizer.grammar.parser import ParseError

        try:
            tokens = Lexer().tokenize(source)
            node = Parser(tokens).parse()
        except (SyntaxError, ParseError) as e:
            return {
                "valid": False,
                "errors": [str(e)],
                "warnings": [],
            }

        result = Validator().validate(node)
        return {
            "valid": result.valid,
            "errors": result.errors,
            "warnings": result.warnings,
            "ast_repr": repr(node),
            "node_type": type(node).__name__,
        }

    def dry_run(self, source: str) -> list[dict]:
        """Parse a workflow and return what each step would do (no execution).

        Returns list of step dicts with agent, action, target, params.
        """
        if not self._optimizer_ok:
            raise RuntimeError("prompt-optimizer not installed. Install with: pip install prompt-optimizer")

        from prompt_optimizer.grammar import Lexer, Parser
        from prompt_optimizer.grammar.ast_nodes import (
            ConditionalNode, DirectiveNode, ParallelBlockNode,
            PipelineNode, ProgramNode, SequentialBlockNode,
        )

        tokens = Lexer().tokenize(source)
        node = Parser(tokens).parse()
        steps: list[dict] = []
        self._collect_steps(node, steps)
        return steps

    def _collect_steps(self, node, steps: list[dict]) -> None:
        from prompt_optimizer.grammar.ast_nodes import (
            ConditionalNode, DirectiveNode, ParallelBlockNode,
            PipelineNode, ProgramNode, SequentialBlockNode,
        )

        if isinstance(node, DirectiveNode):
            agent = node.recipient.agent_code if node.recipient else ""
            params = {p.key: p.value for p in node.params.params} if node.params else {}
            steps.append({
                "agent": agent,
                "action": node.action,
                "target": str(node.target) if node.target else "",
                "params": params,
                "priority": node.priority.level if node.priority else None,
                "modifiers": [m.name for m in node.modifiers] if node.modifiers else [],
                "type": "directive",
            })
        elif isinstance(node, PipelineNode):
            for step in node.directives:
                self._collect_steps(step, steps)
        elif isinstance(node, ParallelBlockNode):
            par_steps: list[dict] = []
            for branch in node.branches:
                branch_steps: list[dict] = []
                self._collect_steps(branch, branch_steps)
                par_steps.extend(branch_steps)
            steps.append({
                "type": "parallel",
                "branches": par_steps,
            })
        elif isinstance(node, SequentialBlockNode):
            for step in node.steps:
                self._collect_steps(step, steps)
        elif isinstance(node, ProgramNode):
            for stmt in node.statements:
                self._collect_steps(stmt, steps)
        elif isinstance(node, ConditionalNode):
            steps.append({
                "type": "conditional",
                "condition": str(node.condition),
            })

    async def execute_workflow(self, name: str, context: dict | None = None) -> Any:
        """Load and execute a .ail workflow file.

        Uses an echo adapter that logs what each agent would do.
        For real execution, use OptimizedCommunicator in C-Suite.

        Raises:
            RuntimeError: If prompt-optimizer is not installed.
            FileNotFoundError: If the workflow doesn't exist.
        """
        if not self._optimizer_ok:
            raise RuntimeError("prompt-optimizer not installed. Install with: pip install prompt-optimizer")

        from prompt_optimizer.grammar import Lexer, Parser
        from prompt_optimizer.runtime import Executor, ExecutionContext

        source = self.load_workflow(name)
        tokens = Lexer().tokenize(source)
        node = Parser(tokens).parse()

        adapter = _EchoAdapter()
        executor = Executor(adapter)
        ctx = ExecutionContext()
        if context:
            ctx.blackboard.update(context.get("blackboard", {}))
            ctx.variables.update(context.get("variables", {}))

        return await executor.execute(node, ctx)

    def workflow_to_tasks(self, name: str, business_description: str = "") -> list[dict]:
        """Convert a .ail workflow's steps into C-Suite task dicts.

        Each task has: area, action, description — matching the format
        expected by the C-Suite Arclane bridge endpoint.
        """
        source = self.load_workflow(name)
        steps = self.dry_run(source)
        return self._steps_to_tasks(steps, business_description)

    def _steps_to_tasks(self, steps: list[dict], description: str) -> list[dict]:
        """Flatten dry-run steps into C-Suite task dicts."""
        tasks: list[dict] = []
        for step in steps:
            if step.get("type") == "directive":
                tasks.append(self._directive_to_task(step, description))
            elif step.get("type") == "parallel":
                for branch in step.get("branches", []):
                    if branch.get("type") == "directive":
                        tasks.append(self._directive_to_task(branch, description))
            elif step.get("type") == "conditional":
                pass  # Skip conditionals in task conversion
        return tasks

    def _directive_to_task(self, step: dict, description: str) -> dict:
        """Convert a single directive step to a C-Suite task dict."""
        agent = step.get("agent", "").lower()
        action = step.get("action", "").lower()
        target = step.get("target", "")
        params = step.get("params", {})

        # Map agent to area
        area = _AGENT_AREA_MAP.get(agent, "general")

        # Build descriptive task description
        parts = [f"{action.replace('_', ' ')} {target}".strip()]
        if params:
            param_str = ", ".join(f"{k}={v}" for k, v in params.items())
            parts.append(f"({param_str})")
        if description:
            parts.append(f"for: {description}")

        return {
            "area": area,
            "action": f"{agent}_{action}" if agent else action,
            "description": " ".join(parts),
        }

    def workflow_for_template(self, template: str | None) -> str | None:
        """Get the workflow name matching a business template.

        Returns the workflow name if found, None otherwise.
        """
        mapping = {
            "content-site": "content_site_cycle",
            "saas-app": "saas_app_cycle",
            "landing-page": "landing_page_cycle",
        }
        name = mapping.get(template, "default_cycle")
        if name in self.list_workflows():
            return name
        return None


# Backward-compat alias
AILWorkflowService = WorkflowService


class _EchoAdapter:
    """Mock adapter that echoes what each directive would do."""

    async def execute_directive(self, agent, action, target, params, constraints, context):
        log.info("Workflow echo: @%s %s %s %s", agent, action, target, params)
        return {
            "agent": agent,
            "action": action,
            "target": str(target) if target else "",
            "params": params,
            "status": "echo",
        }

    async def evaluate_condition(self, condition, context):
        return True

    async def on_retry(self, agent, action, attempt, error):
        pass
