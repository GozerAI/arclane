"""Intake pipeline — translates a business description into an agent task plan.

Takes freeform user input like "I want a SaaS that helps dog groomers manage
appointments" and produces a structured plan that CoS can delegate across
the executive team.
"""

from arclane.core.logging import get_logger
from arclane.engine.executive_prompts import intake_instruction_packet

log = get_logger("intake")


def build_intake_brief(
    description: str,
    website_summary: str | None = None,
    website_url: str | None = None,
) -> dict:
    """Build a structured intake packet for the orchestrator and specialists."""
    packet = intake_instruction_packet()
    business_context = description.strip()
    if website_summary:
        business_context = f"{business_context}\n\nWebsite baseline: {website_summary}".strip()
    elif website_url:
        business_context = f"{business_context}\n\nWebsite URL: {website_url}".strip()

    return {
        "instructions": packet["required_research"],
        "business_context": business_context,
        "program_type": "existing_business" if website_url else "new_venture",
        "working_day_definition": "One working day equals one nightly execution cycle for one business.",
        "visible_proof_targets": [
            "market research report",
            "mission and positioning brief",
            "homepage or social asset",
            "operational launch/provisioning update",
        ],
        "provisioning_requirements": [
            "public URL or website surface",
            "business inbox and address configuration",
            "starter growth channel output",
            "follow-up or launch workflow",
        ],
    }


def build_task_plan(
    description: str,
    template: str | None = None,
    website_summary: str | None = None,
    website_url: str | None = None,
) -> dict:
    """Convert a business description into a first-cycle task plan.

    This plan gets sent to C-Suite's CoS, who routes subtasks to
    the relevant executives.
    """
    intake_brief = build_intake_brief(description, website_summary=website_summary, website_url=website_url)
    context_suffix = ""
    if website_summary:
        context_suffix = f" Existing site context: {website_summary}"
    elif website_url:
        context_suffix = f" Existing site URL: {website_url}"

    strategy_description = (
        "Create the strategic operating brief for this business. "
        f"Define the mission, offer, target customer, wedge, and launch priorities. "
        f"Business context: {description}.{context_suffix}"
    )
    market_description = "Identify competitors, market size, and positioning opportunities"
    content_description = "Generate initial blog post, social media profiles, and landing page copy"
    operations_description = (
        "Configure launch workflows, account for provisioning, and define the first operational loop "
        "for content publishing, lead capture, and follow-up"
    )

    if website_summary or website_url:
        market_description = (
            "Assess the current site, identify competitor gaps, and recommend positioning improvements"
        )
        content_description = (
            "Rewrite the homepage offer, sharpen conversion copy, and produce one publishable growth asset"
        )
        operations_description = (
            "Recommend funnel, CRM, and follow-up workflow improvements that increase conversion speed, "
            "while accounting for provisioning state and launch dependencies"
        )

    plan = {
        "phase": "initial_setup",
        "business_description": description,
        "intake_brief": intake_brief,
        "template": template or "content-site",
        "tasks": [
            {
                "area": "strategy",
                "action": "analyze_business_model",
                "description": strategy_description,
                "brief": "Produce the core business thesis and mission.",
            },
            {
                "area": "market_research",
                "action": "competitive_analysis",
                "description": f"{market_description}. Business context: {description}{context_suffix}",
                "brief": "Research the market, competitors, and gaps Arclane can exploit quickly.",
            },
            {
                "area": "content",
                "action": "create_initial_content",
                "description": f"{content_description}. Business context: {description}{context_suffix}",
                "brief": "Create a visible asset that proves momentum to the user immediately.",
            },
            {
                "area": "operations",
                "action": "setup_workflows",
                "description": f"{operations_description}. Business context: {description}{context_suffix}",
                "brief": "Tie workflow recommendations to provisioning so the business feels live fast.",
            },
        ],
    }

    log.info("Task plan built: %d tasks for template '%s'", len(plan["tasks"]), plan["template"])
    return plan
