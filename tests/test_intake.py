"""Test intake pipeline."""

from arclane.engine.intake import build_task_plan


def test_build_task_plan_default_template():
    plan = build_task_plan("A SaaS for dog groomers")
    assert plan["phase"] == "initial_setup"
    assert plan["template"] == "content-site"
    assert len(plan["tasks"]) == 4
    assert any(t["area"] == "strategy" for t in plan["tasks"])
    assert any(t["area"] == "content" for t in plan["tasks"])


def test_build_task_plan_custom_template():
    plan = build_task_plan("E-commerce store", template="saas-app")
    assert plan["template"] == "saas-app"


def test_build_task_plan_includes_description():
    desc = "AI-powered resume builder"
    plan = build_task_plan(desc)
    strategy_task = next(t for t in plan["tasks"] if t["area"] == "strategy")
    assert desc in strategy_task["description"]


def test_build_task_plan_includes_website_context():
    plan = build_task_plan(
        "Optimize a consulting site",
        website_summary="Title: Acme Consulting. Key headings: Fractional CFO services; Case studies.",
        website_url="https://acme.example",
    )
    descriptions = " ".join(task["description"] for task in plan["tasks"])
    assert "Fractional CFO services" in descriptions
    assert "optimize" in descriptions.lower() or "rewrite" in descriptions.lower()


def test_build_task_plan_carries_intake_brief_and_provisioning_requirements():
    plan = build_task_plan("An AI automation service for local operators")
    assert "intake_brief" in plan
    assert "provisioning_requirements" in plan["intake_brief"]
    assert any("inbox" in item for item in plan["intake_brief"]["provisioning_requirements"])
    operations_task = next(t for t in plan["tasks"] if t["area"] == "operations")
    assert "provisioning" in operations_task["description"].lower()
