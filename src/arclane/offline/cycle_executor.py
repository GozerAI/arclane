"""Offline cycle execution with local/deterministic fallbacks.

Item 772: When LLM endpoints and C-Suite bridge are unreachable, cycles
still execute using deterministic task generation and template-based outputs.
No network calls required -- all logic is rule-based.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Deterministic task definitions per area
_AREA_TASKS: Dict[str, List[Dict[str, str]]] = {
    "strategy": [
        {"action": "define_mission", "prompt": "Define core mission statement"},
        {"action": "identify_wedge", "prompt": "Identify competitive wedge"},
        {"action": "set_priorities", "prompt": "Set top 3 priorities for this cycle"},
    ],
    "market_research": [
        {"action": "competitor_scan", "prompt": "Identify top 3 competitors and their positioning"},
        {"action": "opportunity_map", "prompt": "Map market gaps and opportunities"},
        {"action": "audience_profile", "prompt": "Define target audience segments"},
    ],
    "content": [
        {"action": "headline_pack", "prompt": "Generate 5 headline variations for the offer"},
        {"action": "landing_copy", "prompt": "Write landing page hero copy"},
        {"action": "email_welcome", "prompt": "Draft welcome email sequence outline"},
    ],
    "engineering": [
        {"action": "tech_stack", "prompt": "Recommend tech stack for the product"},
        {"action": "mvp_scope", "prompt": "Define MVP feature scope"},
        {"action": "security_review", "prompt": "Outline basic security requirements"},
    ],
    "finance": [
        {"action": "cost_model", "prompt": "Draft initial cost model"},
        {"action": "pricing_strategy", "prompt": "Suggest pricing tiers"},
        {"action": "revenue_forecast", "prompt": "Project 3-month revenue forecast"},
    ],
    "operations": [
        {"action": "launch_checklist", "prompt": "Create launch checklist"},
        {"action": "workflow_setup", "prompt": "Define daily operational workflow"},
        {"action": "kpi_dashboard", "prompt": "Identify key performance indicators"},
    ],
}

# Deterministic output templates for when no LLM is available
_DETERMINISTIC_OUTPUTS: Dict[str, str] = {
    "define_mission": "Mission: Help {business_name} deliver value to its target market through {template} approach.",
    "identify_wedge": "Competitive wedge: {business_name} differentiates through direct customer engagement and rapid iteration.",
    "set_priorities": "Priority 1: Validate core offer. Priority 2: Build initial audience. Priority 3: Establish feedback loop.",
    "competitor_scan": "Competitor analysis queued for {business_name}. Key differentiators to be identified when online.",
    "opportunity_map": "Market opportunities for {business_name}: direct-to-consumer, content marketing, community building.",
    "audience_profile": "Primary audience: professionals seeking {description}. Secondary: early adopters in adjacent markets.",
    "headline_pack": "Headlines for {business_name}: 1) '{business_name} - Built for You' 2) 'The Smarter Way to {tagline}' 3) 'Start with {business_name}' 4) 'Why {business_name}?' 5) '{business_name}: No Compromise'",
    "landing_copy": "{business_name} helps you achieve more with less. Get started today and see the difference.",
    "email_welcome": "Welcome to {business_name}! Here's what to expect: Day 1 - Getting started guide. Day 3 - Tips and tricks. Day 7 - Your first milestone.",
    "tech_stack": "Recommended stack: Node.js/Express backend, static frontend, SQLite for MVP, Docker for deployment.",
    "mvp_scope": "MVP scope: Authentication, core feature, basic dashboard, feedback mechanism.",
    "security_review": "Security baseline: HTTPS, input validation, rate limiting, secure password storage.",
    "cost_model": "Estimated monthly costs: Infrastructure $50-200, domains $15/yr, email $10/mo, monitoring $0-50.",
    "pricing_strategy": "Suggested tiers: Free (limited), Starter $29/mo, Pro $99/mo, Enterprise custom.",
    "revenue_forecast": "Month 1: $0-500 (validation). Month 2: $500-2000 (early adopters). Month 3: $2000-5000 (growth).",
    "launch_checklist": "Launch checklist: [ ] Domain configured, [ ] Landing page live, [ ] Email capture working, [ ] Analytics installed, [ ] First content published.",
    "workflow_setup": "Daily workflow: Morning - check metrics, Midday - content/engagement, Afternoon - product iteration, Evening - planning.",
    "kpi_dashboard": "Key KPIs: Website visitors, signup conversion rate, active users, MRR, churn rate, NPS.",
}


@dataclass
class OfflineTaskResult:
    """Result of a single offline task execution."""

    area: str
    action: str
    status: str = "completed"  # completed | skipped | error
    output: str = ""
    is_deterministic: bool = True
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "area": self.area,
            "action": self.action,
            "status": self.status,
            "output": self.output,
            "is_deterministic": self.is_deterministic,
            "executed_at": self.executed_at.isoformat(),
            "error": self.error,
        }


@dataclass
class OfflineCycleResult:
    """Result of a full offline cycle execution."""

    business_name: str
    slug: str
    cycle_id: Optional[int] = None
    status: str = "completed"
    tasks: List[OfflineTaskResult] = field(default_factory=list)
    areas_covered: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    is_offline: bool = True
    warnings: List[str] = field(default_factory=list)

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "completed")

    @property
    def success_rate(self) -> float:
        if not self.tasks:
            return 0.0
        return self.completed_count / self.task_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "business_name": self.business_name,
            "slug": self.slug,
            "cycle_id": self.cycle_id,
            "status": self.status,
            "task_count": self.task_count,
            "completed_count": self.completed_count,
            "success_rate": round(self.success_rate, 3),
            "areas_covered": self.areas_covered,
            "tasks": [t.to_dict() for t in self.tasks],
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "is_offline": self.is_offline,
            "warnings": self.warnings,
        }


class OfflineCycleExecutor:
    """Execute business cycles offline using deterministic task generation.

    When LLM endpoints and C-Suite bridge are unreachable, this executor
    produces useful, structured output using rule-based logic and templates.
    Results are clearly marked as deterministic so they can be enriched when
    connectivity returns.
    """

    def __init__(
        self,
        areas: Optional[List[str]] = None,
        local_model_fn: Optional[Any] = None,
    ):
        """
        Args:
            areas: Which task areas to execute. Defaults to all.
            local_model_fn: Optional callable(system_prompt, user_prompt) -> str
                           for local LLM inference (e.g., llama.cpp, ollama).
                           If None, uses deterministic templates.
        """
        self._areas = areas or list(_AREA_TASKS.keys())
        self._local_model_fn = local_model_fn

    @property
    def has_local_model(self) -> bool:
        return self._local_model_fn is not None

    def execute(
        self,
        business_name: str,
        slug: str,
        description: str = "",
        template: str = "",
        cycle_id: Optional[int] = None,
        areas: Optional[List[str]] = None,
        task_description: Optional[str] = None,
    ) -> OfflineCycleResult:
        """Execute a full offline cycle for a business.

        Args:
            business_name: The business name.
            slug: The business slug.
            description: Business description for template interpolation.
            template: Template type (content-site, saas-app, etc.)
            cycle_id: Optional cycle ID for tracking.
            areas: Override which areas to execute.
            task_description: Optional specific task to execute instead of full cycle.
        """
        target_areas = areas or self._areas
        context = {
            "business_name": business_name,
            "slug": slug,
            "description": description or business_name,
            "template": template or "general",
            "tagline": description[:60] if description else business_name,
        }

        result = OfflineCycleResult(
            business_name=business_name,
            slug=slug,
            cycle_id=cycle_id,
        )

        if task_description:
            # Execute a single specific task
            task_result = self._execute_specific_task(task_description, context)
            result.tasks.append(task_result)
            result.areas_covered = [task_result.area]
        else:
            # Execute all area tasks
            for area in target_areas:
                if area not in _AREA_TASKS:
                    result.warnings.append(f"unknown_area:{area}")
                    continue

                result.areas_covered.append(area)
                for task_def in _AREA_TASKS[area]:
                    task_result = self._execute_task(area, task_def, context)
                    result.tasks.append(task_result)

        result.completed_at = datetime.now(timezone.utc)
        result.status = "completed" if result.completed_count > 0 else "failed"

        if not self.has_local_model:
            result.warnings.append("deterministic_mode_no_llm")

        logger.info(
            "Offline cycle for %s: %d/%d tasks completed",
            slug, result.completed_count, result.task_count,
        )
        return result

    def execute_area(
        self,
        area: str,
        business_name: str,
        slug: str,
        description: str = "",
        template: str = "",
    ) -> List[OfflineTaskResult]:
        """Execute all tasks for a single area."""
        context = {
            "business_name": business_name,
            "slug": slug,
            "description": description or business_name,
            "template": template or "general",
            "tagline": description[:60] if description else business_name,
        }
        tasks_def = _AREA_TASKS.get(area, [])
        results = []
        for task_def in tasks_def:
            results.append(self._execute_task(area, task_def, context))
        return results

    def list_areas(self) -> List[str]:
        """List all available task areas."""
        return list(_AREA_TASKS.keys())

    def list_tasks(self, area: str) -> List[Dict[str, str]]:
        """List available tasks for an area."""
        return _AREA_TASKS.get(area, [])

    def _execute_task(
        self,
        area: str,
        task_def: Dict[str, str],
        context: Dict[str, str],
    ) -> OfflineTaskResult:
        """Execute a single task, trying local model first, then deterministic."""
        action = task_def["action"]
        prompt = task_def["prompt"]

        # Try local model first
        if self._local_model_fn is not None:
            try:
                system_prompt = f"You are an expert {area} specialist for a business called {context.get('business_name', 'the business')}."
                output = self._local_model_fn(system_prompt, prompt)
                if output and isinstance(output, str) and output.strip():
                    return OfflineTaskResult(
                        area=area,
                        action=action,
                        output=output.strip(),
                        is_deterministic=False,
                    )
            except Exception as exc:
                logger.warning("Local model failed for %s/%s: %s", area, action, exc)

        # Fall back to deterministic template
        template = _DETERMINISTIC_OUTPUTS.get(action, f"Task '{action}' for {context.get('business_name', 'business')} queued for execution when online.")
        output = self._interpolate(template, context)

        return OfflineTaskResult(
            area=area,
            action=action,
            output=output,
            is_deterministic=True,
        )

    def _execute_specific_task(
        self,
        task_description: str,
        context: Dict[str, str],
    ) -> OfflineTaskResult:
        """Execute a specific user-described task."""
        if self._local_model_fn is not None:
            try:
                system_prompt = f"You are a business operations specialist for {context.get('business_name', 'the business')}."
                output = self._local_model_fn(system_prompt, task_description)
                if output and isinstance(output, str) and output.strip():
                    return OfflineTaskResult(
                        area="general",
                        action="custom_task",
                        output=output.strip(),
                        is_deterministic=False,
                    )
            except Exception as exc:
                logger.warning("Local model failed for custom task: %s", exc)

        output = f"Task received for {context.get('business_name', 'business')}: {task_description}. Will be processed when full capabilities are available."
        return OfflineTaskResult(
            area="general",
            action="custom_task",
            output=output,
            is_deterministic=True,
        )

    @staticmethod
    def _interpolate(template: str, context: Dict[str, str]) -> str:
        """Simple {var} interpolation."""
        result = template
        for key, value in context.items():
            result = result.replace(f"{{{key}}}", value)
        return result
