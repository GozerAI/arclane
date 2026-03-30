"""Daily steering — morning decisioning message for the AI cofounder loop."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import Business, Content, Cycle, Milestone

log = get_logger("daily_steering")


async def generate_steering_brief(
    business: Business,
    session: AsyncSession,
) -> dict:
    """Build the daily steering brief for a business.

    Returns a dict with:
      - last_cycle_summary: what happened in the most recent cycle
      - content_produced: list of content titles created
      - milestones_hit: milestones completed recently
      - today_plan: what the next cycle intends to work on
      - health_snapshot: current health score and phase
      - steering_prompt: the question to ask the founder
    """
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)

    # Last cycle
    last_cycle_result = await session.execute(
        select(Cycle)
        .where(Cycle.business_id == business.id)
        .order_by(Cycle.created_at.desc())
        .limit(1)
    )
    last_cycle = last_cycle_result.scalar_one_or_none()

    last_cycle_summary = "No cycles have run yet."
    if last_cycle:
        tasks_done = 0
        if last_cycle.result and isinstance(last_cycle.result, dict):
            tasks = last_cycle.result.get("tasks", [])
            tasks_done = len(tasks) if isinstance(tasks, list) else 0
        last_cycle_summary = (
            f"Last cycle ({last_cycle.trigger}) finished with status: {last_cycle.status}. "
            f"{tasks_done} tasks completed."
        )

    # Content produced in last 24h
    content_result = await session.execute(
        select(Content.title, Content.content_type)
        .where(
            Content.business_id == business.id,
            Content.created_at >= yesterday,
        )
        .order_by(Content.created_at.desc())
        .limit(10)
    )
    content_produced = [
        {"title": row[0] or "Untitled", "type": row[1]}
        for row in content_result.all()
    ]

    # Milestones completed recently
    milestone_result = await session.execute(
        select(Milestone.title, Milestone.key)
        .where(
            Milestone.business_id == business.id,
            Milestone.status == "completed",
            Milestone.completed_at >= yesterday,
        )
    )
    milestones_hit = [{"title": row[0], "key": row[1]} for row in milestone_result.all()]

    # Today's plan — next milestone(s) in the roadmap
    next_milestones_result = await session.execute(
        select(Milestone.title, Milestone.key, Milestone.category)
        .where(
            Milestone.business_id == business.id,
            Milestone.status.in_(["pending", "in_progress"]),
            Milestone.phase_number == (business.current_phase or 1),
        )
        .order_by(Milestone.due_day.asc())
        .limit(3)
    )
    today_plan = [
        {"title": row[0], "key": row[1], "category": row[2]}
        for row in next_milestones_result.all()
    ]

    if not today_plan:
        today_plan_text = "No pending milestones — the program will select the next best task based on your health scores."
    else:
        items = ", ".join(t["title"] for t in today_plan)
        today_plan_text = f"Next up: {items}"

    # Phase info
    phase_names = {
        0: "Not started",
        1: "Foundation",
        2: "Validation",
        3: "Growth",
        4: "Scale-Ready",
        5: "Graduated",
    }
    phase_name = phase_names.get(business.current_phase or 0, "Unknown")

    # Steering prompt
    steering_prompt = (
        "Reply to this message with any direction for today's work. "
        "You can adjust priorities, request specific content, redirect research, "
        "or tell me to stay the course. Your input shapes tonight's cycle."
    )

    return {
        "business_name": business.name,
        "business_slug": business.slug,
        "day": business.roadmap_day or 0,
        "phase": phase_name,
        "phase_number": business.current_phase or 0,
        "health_score": business.health_score,
        "last_cycle_summary": last_cycle_summary,
        "content_produced": content_produced,
        "milestones_hit": milestones_hit,
        "today_plan": today_plan,
        "today_plan_text": today_plan_text,
        "steering_prompt": steering_prompt,
    }
