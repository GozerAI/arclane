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
