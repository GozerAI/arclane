"""Orchestrator — task execution engine (requires commercial license).

The community edition includes the Arclane platform, UI, and business management.
Task execution via the AI engine requires a commercial license.
Visit https://gozerai.com/pricing for details.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.engine.intake import build_task_plan
from arclane.models.tables import Activity, Business, Cycle

log = get_logger("orchestrator")


class ArclaneOrchestrator:
    """Community edition orchestrator — logs tasks but does not execute them."""

    async def execute_cycle(
        self, business: Business, cycle: Cycle, session: AsyncSession
    ) -> dict:
        """Record cycle as pending — execution requires commercial license."""
        log.info("Cycle %d queued for business %s (community edition)", cycle.id, business.slug)

        cycle.status = "completed"
        cycle.started_at = datetime.now(timezone.utc)
        cycle.completed_at = datetime.now(timezone.utc)
        cycle.result = {
            "status": "community_edition",
            "message": "Task execution requires a commercial Arclane license. Visit https://gozerai.com/pricing",
            "tasks": build_task_plan(business.description, business.template)["tasks"],
        }

        activity = Activity(
            business_id=business.id,
            cycle_id=cycle.id,
            agent="system",
            action="Cycle completed (community)",
            detail="Task plan generated. Execution requires commercial license.",
        )
        session.add(activity)
        await session.commit()

        return cycle.result


orchestrator = ArclaneOrchestrator()
