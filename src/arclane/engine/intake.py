"""Intake pipeline — translates a business description into an agent task plan.

Takes freeform user input like "I want a SaaS that helps dog groomers manage
appointments" and produces a structured plan that CoS can delegate across
the executive team.
"""

from arclane.core.logging import get_logger

log = get_logger("intake")


def build_task_plan(description: str, template: str | None = None) -> dict:
    """Convert a business description into a first-cycle task plan.

    This plan gets sent to C-Suite's CoS, who routes subtasks to
    the relevant executives.
    """
    plan = {
        "phase": "initial_setup",
        "business_description": description,
        "template": template or "content-site",
        "tasks": [
            {
                "area": "strategy",
                "action": "analyze_business_model",
                "description": f"Analyze this business idea and create a strategic plan: {description}",
            },
            {
                "area": "market_research",
                "action": "competitive_analysis",
                "description": "Identify competitors, market size, and positioning opportunities",
            },
            {
                "area": "content",
                "action": "create_initial_content",
                "description": "Generate initial blog post, social media profiles, and landing page copy",
            },
            {
                "area": "operations",
                "action": "setup_workflows",
                "description": "Configure automated workflows for content publishing and lead capture",
            },
        ],
    }

    log.info("Task plan built: %d tasks for template '%s'", len(plan["tasks"]), plan["template"])
    return plan
