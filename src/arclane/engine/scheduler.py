"""Scheduler — nightly autonomous cycles, monthly credit reset."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from arclane.core.config import settings
from arclane.core.database import async_session
from arclane.core.logging import get_logger
from arclane.engine.orchestrator import orchestrator
from arclane.models.tables import Business, Cycle

log = get_logger("scheduler")

_scheduler: AsyncIOScheduler | None = None

PLAN_CREDITS = {
    "starter": 5,
    "pro": 20,
    "enterprise": 100,
}


async def _nightly_cycle():
    """Run one autonomous cycle for every active business with credits."""
    log.info("Nightly cycle starting")

    async with async_session() as session:
        result = await session.execute(
            select(Business).where(Business.plan != "cancelled")
        )
        businesses = result.scalars().all()

    log.info("Running nightly cycle for %d businesses", len(businesses))

    for business in businesses:
        # Skip businesses with no credits
        if business.credits_remaining + business.credits_bonus <= 0:
            log.info("Skipping %s — no credits", business.slug)
            continue

        try:
            async with async_session() as session:
                biz = await session.get(Business, business.id)

                # Deduct credit
                if biz.credits_bonus > 0:
                    biz.credits_bonus -= 1
                else:
                    biz.credits_remaining -= 1

                cycle = Cycle(
                    business_id=biz.id,
                    trigger="nightly",
                    status="pending",
                )
                session.add(cycle)
                await session.commit()
                await session.refresh(cycle)

                await orchestrator.execute_cycle(biz, cycle, session)

            log.info("Nightly cycle for %s: %s", business.slug, cycle.status)
        except Exception:
            log.exception("Nightly cycle failed for %s", business.slug)


async def _monthly_credit_reset():
    """Reset credits for all active subscriptions on the 1st of each month."""
    log.info("Monthly credit reset starting")

    async with async_session() as session:
        result = await session.execute(
            select(Business).where(
                Business.plan.in_(list(PLAN_CREDITS.keys()))
            )
        )
        businesses = result.scalars().all()

        reset_count = 0
        for biz in businesses:
            credits = PLAN_CREDITS.get(biz.plan)
            if credits:
                biz.credits_remaining = credits
                reset_count += 1

        await session.commit()

    log.info("Monthly credit reset: %d businesses refreshed", reset_count)


async def _container_health_check():
    """Check health of all deployed containers."""
    from arclane.provisioning.deploy import check_container_health

    async with async_session() as session:
        result = await session.execute(
            select(Business).where(
                Business.app_deployed == True,  # noqa: E712
                Business.container_id != None,  # noqa: E711
            )
        )
        businesses = result.scalars().all()

    for biz in businesses:
        health = await check_container_health(biz.slug)
        if not health.get("running", False) and health["status"] != "unknown":
            log.warning(
                "Container unhealthy: %s (status=%s)",
                biz.slug, health["status"],
            )


def start_scheduler():
    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _nightly_cycle,
        "cron",
        hour=settings.nightly_hour,
        minute=settings.nightly_minute,
        id="nightly_cycle",
    )
    _scheduler.add_job(
        _monthly_credit_reset,
        "cron",
        day=1,
        hour=0,
        minute=0,
        id="monthly_credit_reset",
    )
    _scheduler.add_job(
        _container_health_check,
        "interval",
        minutes=15,
        id="container_health_check",
    )
    _scheduler.start()
    log.info(
        "Scheduler started — nightly at %02d:%02d, monthly reset on 1st, container health every 15m",
        settings.nightly_hour,
        settings.nightly_minute,
    )


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler stopped")
