"""Tests for the persisted post-intake operating plan."""

from arclane.engine.operating_plan import build_operating_plan, enqueue_add_on, reserve_included_cycle


def test_operating_plan_includes_tasks_recommendations_and_storage():
    plan = build_operating_plan(
        name="Loopstack",
        slug="loopstack",
        description="An automation service for small operators",
        template="content-site",
    )

    assert plan["program_type"] == "new_venture"
    assert plan["working_day_model"]["definition"] == "One working day equals one nightly execution cycle for one business."
    assert len(plan["agent_tasks"]) == 4
    # All Day 1 tasks are single-day for instant delivery
    assert all(t["duration_days"] == 1 for t in plan["agent_tasks"])
    # No dependency chains — all run sequentially in one initial cycle
    assert all(not t["depends_on"] for t in plan["agent_tasks"])
    assert plan["user_recommendations"]
    assert plan["add_on_offers"]
    assert plan["provisioning"]["mailbox"] == "loopstack@arclane.cloud"
    assert plan["code_storage"]["workspace_path"].endswith("loopstack")
    assert plan["code_storage"]["manifest_path"].endswith("arclane-workspace.json")


def test_operating_plan_switches_recommendations_for_existing_sites():
    plan = build_operating_plan(
        name="Acme",
        slug="acme",
        description="Optimize the existing business",
        template="landing-page",
        website_url="https://acme.example",
        website_summary="Consulting offer with vague messaging.",
    )

    titles = [item["title"] for item in plan["user_recommendations"]]
    assert "Approve the homepage rewrite" in titles
    assert plan["program_type"] == "existing_business"
    assert plan["provisioning"]["public_url"] == "https://acme.example"


def test_enqueue_add_on_inserts_queue_cutting_work():
    plan = build_operating_plan(
        name="Loopstack",
        slug="loopstack",
        description="An automation service for small operators",
        template="content-site",
    )
    plan["add_on_offers"][0]["status"] = "available"

    updated = enqueue_add_on(plan, "deep-market-dive")

    assert updated["agent_tasks"][0]["kind"] == "add_on"
    assert updated["agent_tasks"][0]["output_key"] == "deep-market-dive"
    assert updated["agent_tasks"][0]["working_days_required"] == 3
    assert updated["agent_tasks"][0]["supersedes_queue"] is True
    assert updated["agent_tasks"][0]["included_cycles_total"] == 3
    assert updated["agent_tasks"][0]["included_cycles_remaining"] == 3
    assert updated["add_on_offers"][0]["status"] == "purchased"


def test_reserve_included_cycle_decrements_purchased_add_on_capacity():
    plan = build_operating_plan(
        name="Loopstack",
        slug="loopstack",
        description="An automation service for small operators",
        template="content-site",
    )
    plan["add_on_offers"][0]["status"] = "available"
    plan = enqueue_add_on(plan, "deep-market-dive")

    updated, consumed = reserve_included_cycle(plan, "addon-market-01")

    assert consumed is True
    assert updated["agent_tasks"][0]["included_cycles_remaining"] == 2
