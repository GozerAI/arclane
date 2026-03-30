"""Scheduler - nightly autonomous cycles, monthly working day reset, stuck cycle recovery."""

import asyncio
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from arclane.billing.policy import RECURRING_PLAN_WORKING_DAYS
from arclane.core.config import settings
from arclane.core.database import async_session
from arclane.core.logging import get_logger
from arclane.engine.operating_plan import reserve_included_cycle
from arclane.engine.orchestrator import orchestrator
from arclane.models.tables import Activity, Business, Content, Cycle, FailedWebhook

log = get_logger("scheduler")

STUCK_CYCLE_THRESHOLD_MINUTES = 30

_scheduler: AsyncIOScheduler | None = None

PLAN_WORKING_DAYS = RECURRING_PLAN_WORKING_DAYS

PLAN_PRIORITY = {
    "scale": 0,
    "enterprise": 0,
    "growth": 1,
    "pro": 2,
    "starter": 3,
    "preview": 4,
}


async def _run_single_nightly(business_id: int, semaphore: asyncio.Semaphore):
    """Run a single nightly cycle for one business, bounded by semaphore."""
    async with semaphore:
        try:
            async with async_session() as session:
                biz = await session.get(Business, business_id)
                if not biz:
                    return
                log.info("Running nightly for %s (plan=%s)", biz.slug, biz.plan)

                # Consult cycle optimizer before spending a working day
                from arclane.autonomy.cycle_optimizer import evaluate_nightly
                decision = await evaluate_nightly(biz, session)
                if not decision.should_run:
                    log.info("Optimizer skipped %s: %s", biz.slug, decision.reason)
                    return

                next_task = orchestrator.next_queue_task(biz)
                used_included_cycle = False
                if next_task and next_task.get("kind") == "add_on":
                    operating_plan = (biz.agent_config or {}).get("operating_plan") or {}
                    updated_plan, used_included_cycle = reserve_included_cycle(
                        operating_plan,
                        next_task.get("queue_task_key") or next_task.get("key", ""),
                    )
                    if used_included_cycle:
                        updated_agent_config = dict(biz.agent_config or {})
                        updated_agent_config["operating_plan"] = updated_plan
                        biz.agent_config = updated_agent_config

                if not used_included_cycle and biz.working_days_remaining + biz.working_days_bonus <= 0:
                    log.info("Skipping %s - no working days", biz.slug)
                    return

                if not used_included_cycle:
                    if biz.working_days_bonus > 0:
                        biz.working_days_bonus -= 1
                    else:
                        biz.working_days_remaining -= 1

                cycle = Cycle(
                    business_id=biz.id,
                    trigger="nightly",
                    status="pending",
                )
                session.add(cycle)
                await session.commit()
                await session.refresh(cycle)

                await orchestrator.execute_cycle(biz, cycle, session)

                log.info("Nightly cycle for %s: %s", biz.slug, cycle.status)
        except Exception:
            log.exception("Nightly cycle failed for business_id=%d", business_id)


async def _nightly_cycle():
    """Run one autonomous cycle for every active business with working days."""
    log.info("Nightly cycle starting")

    async with async_session() as session:
        result = await session.execute(
            select(Business.id, Business.plan).where(Business.plan != "cancelled")
        )
        rows = result.all()

    # Higher-tier plans run first (lower priority number = earlier execution)
    rows.sort(key=lambda r: PLAN_PRIORITY.get(r[1], 5))
    business_ids = [row[0] for row in rows]

    log.info("Running nightly cycle for %d businesses", len(business_ids))

    semaphore = asyncio.Semaphore(3)
    await asyncio.gather(
        *(_run_single_nightly(bid, semaphore) for bid in business_ids),
        return_exceptions=True,
    )


async def _monthly_working_day_reset():
    """Reset working days for all active subscriptions on the 1st of each month."""
    log.info("Monthly working day reset starting")

    async with async_session() as session:
        result = await session.execute(
            select(Business).where(
                Business.plan.in_(list(PLAN_WORKING_DAYS.keys()))
            )
        )
        businesses = result.scalars().all()

        reset_count = 0
        for biz in businesses:
            allotment = PLAN_WORKING_DAYS.get(biz.plan)
            if allotment:
                biz.working_days_remaining = allotment
                reset_count += 1

        await session.commit()

    log.info("Monthly working day reset: %d businesses refreshed", reset_count)


async def _recover_stuck_cycles():
    """Detect cycles stuck in 'running' state and mark them failed.

    If a cycle has been running for longer than STUCK_CYCLE_THRESHOLD_MINUTES,
    it almost certainly crashed or hung.  Mark it failed so the business isn't
    blocked and the dashboard doesn't show a perpetual spinner.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_CYCLE_THRESHOLD_MINUTES)

    async with async_session() as session:
        result = await session.execute(
            select(Cycle).where(
                Cycle.status == "running",
                Cycle.started_at < cutoff,
            )
        )
        stuck_cycles = result.scalars().all()

        if not stuck_cycles:
            return

        log.warning("Found %d stuck cycles — recovering", len(stuck_cycles))

        for cycle in stuck_cycles:
            cycle.status = "failed"
            cycle.completed_at = datetime.now(timezone.utc)
            cycle.result = cycle.result or {}
            cycle.result["recovery"] = "auto-recovered by scheduler after timeout"

            session.add(
                Activity(
                    business_id=cycle.business_id,
                    cycle_id=cycle.id,
                    agent="system",
                    action="Cycle recovered",
                    detail=(
                        f"This cycle was running for over {STUCK_CYCLE_THRESHOLD_MINUTES} minutes "
                        "and was automatically marked as failed."
                    ),
                )
            )

        await session.commit()
        log.info("Recovered %d stuck cycles", len(stuck_cycles))


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


async def _publish_scheduled_content():
    """Publish content whose scheduled time has arrived and distribute to channels."""
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(Content).where(
                Content.status == "scheduled",
                Content.published_at != None,  # noqa: E711
                Content.published_at <= now,
            )
        )
        items = result.scalars().all()

        if not items:
            return

        for item in items:
            item.status = "published"

            # Auto-distribute through configured channels
            try:
                biz = await session.get(Business, item.business_id)
                if biz:
                    from arclane.services.distribution_service import distribute_content
                    await distribute_content(biz, item, session)
            except Exception:
                log.warning("Auto-distribution failed for content %d", item.id, exc_info=True)

        await session.commit()
        log.info("Published %d scheduled content items", len(items))


async def _advance_roadmap_days():
    """Advance roadmap_day for all active businesses — runs daily regardless of cycle execution.

    roadmap_day tracks calendar days in the program, not cycles executed.
    This ensures phase deadlines are meaningful even when working days run out.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Business).where(
                Business.plan != "cancelled",
                Business.current_phase != None,  # noqa: E711
                Business.current_phase >= 1,
                Business.current_phase <= 4,  # Don't advance post-graduation
            )
        )
        businesses = result.scalars().all()

        advanced = 0
        for biz in businesses:
            biz.roadmap_day = (biz.roadmap_day or 0) + 1
            advanced += 1

        await session.commit()

    if advanced:
        log.info("Advanced roadmap_day for %d businesses", advanced)


async def _send_weekly_digests():
    """Send weekly digest emails to all active businesses."""
    from arclane.services.advisory_service import generate_weekly_digest
    from arclane.notifications import send_weekly_digest_email

    async with async_session() as session:
        result = await session.execute(
            select(Business).where(Business.plan != "cancelled")
        )
        businesses = result.scalars().all()

    for biz in businesses:
        try:
            async with async_session() as session:
                digest = await generate_weekly_digest(biz, session)
            await send_weekly_digest_email(
                biz.name, biz.owner_email, biz.slug, digest,
            )
        except Exception:
            log.exception("Weekly digest failed for %s", biz.slug)

    log.info("Weekly digests sent for %d businesses", len(businesses))


async def _auto_fill_content_calendars():
    """Pre-generate draft content for upcoming calendar slots — runs after nightly cycles."""
    async with async_session() as session:
        result = await session.execute(
            select(Business).where(
                Business.plan != "cancelled",
                Business.current_phase != None,  # noqa: E711
                Business.current_phase >= 1,
            )
        )
        businesses = result.scalars().all()

    filled_total = 0
    for biz in businesses:
        try:
            async with async_session() as session:
                biz_fresh = await session.get(Business, biz.id)
                if not biz_fresh:
                    continue
                from arclane.services.content_calendar import auto_fill_calendar
                created = await auto_fill_calendar(biz_fresh, session, days_ahead=7, max_drafts=3)
                if created:
                    await session.commit()
                    filled_total += len(created)
        except Exception:
            log.debug("Calendar auto-fill failed for %s", biz.slug, exc_info=True)

    if filled_total:
        log.info("Auto-filled %d content drafts across all businesses", filled_total)


async def _send_daily_steering():
    """Send daily steering/decisioning messages at 6:30am."""
    from arclane.services.daily_steering import generate_steering_brief
    from arclane.notifications import send_daily_steering_email
    from arclane.services.telegram_steering import send_steering_telegram

    async with async_session() as session:
        result = await session.execute(
            select(Business).where(
                Business.plan != "cancelled",
                Business.current_phase != None,  # noqa: E711
                Business.current_phase >= 1,
            )
        )
        businesses = result.scalars().all()

    for biz in businesses:
        try:
            async with async_session() as session:
                brief = await generate_steering_brief(biz, session)

            # Email (always)
            await send_daily_steering_email(
                biz.name, biz.owner_email, biz.slug, brief,
            )

            # Telegram (if configured)
            chat_id = (biz.agent_config or {}).get("telegram_chat_id")
            if chat_id:
                await send_steering_telegram(chat_id, brief)

        except Exception:
            log.exception("Daily steering failed for %s", biz.slug)

    log.info("Daily steering sent for %d businesses", len(businesses))


async def _retry_failed_webhooks():
    """Retry failed webhooks with exponential backoff (max 5 attempts)."""
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(FailedWebhook).where(
                FailedWebhook.resolved == False,  # noqa: E712
                FailedWebhook.next_retry_at <= now,
                FailedWebhook.attempts < FailedWebhook.max_attempts,
            ).limit(20)
        )
        webhooks = result.scalars().all()

        if not webhooks:
            return

        log.info("Retrying %d failed webhooks", len(webhooks))

        for wh in webhooks:
            try:
                from arclane.api.routes.billing import WebhookPayload, _process_webhook_payload
                payload = WebhookPayload.model_validate(wh.payload)
                await _process_webhook_payload(payload, session)
                wh.resolved = True
                log.info("Webhook retry succeeded: id=%d event=%s", wh.id, wh.payload.get("event"))
            except Exception:
                wh.attempts += 1
                # Exponential backoff: 2min, 8min, 32min, 128min, then give up
                backoff_minutes = 2 ** (2 * wh.attempts - 1)
                wh.next_retry_at = now + timedelta(minutes=backoff_minutes)
                wh.error = f"Attempt {wh.attempts} failed"
                if wh.attempts >= wh.max_attempts:
                    wh.resolved = True
                    log.error(
                        "Webhook permanently failed after %d attempts: id=%d",
                        wh.attempts, wh.id,
                    )
                else:
                    log.warning(
                        "Webhook retry %d/%d failed, next at %s: id=%d",
                        wh.attempts, wh.max_attempts, wh.next_retry_at, wh.id,
                    )

        await session.commit()


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
        _monthly_working_day_reset,
        "cron",
        day=1,
        hour=0,
        minute=0,
        id="monthly_working_day_reset",
    )
    _scheduler.add_job(
        _container_health_check,
        "interval",
        minutes=15,
        id="container_health_check",
    )
    _scheduler.add_job(
        _recover_stuck_cycles,
        "interval",
        minutes=10,
        id="recover_stuck_cycles",
    )
    _scheduler.add_job(
        _publish_scheduled_content,
        "interval",
        minutes=5,
        id="publish_scheduled_content",
    )
    _scheduler.add_job(
        _advance_roadmap_days,
        "cron",
        hour=0,
        minute=30,
        id="advance_roadmap_days",
    )
    _scheduler.add_job(
        _send_weekly_digests,
        "cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        id="weekly_digest",
    )
    _scheduler.add_job(
        _auto_fill_content_calendars,
        "cron",
        hour=settings.nightly_hour,
        minute=(settings.nightly_minute + 30) % 60,
        id="auto_fill_calendars",
    )
    _scheduler.add_job(
        _send_daily_steering,
        "cron",
        hour=6,
        minute=30,
        id="daily_steering",
    )
    _scheduler.add_job(
        _retry_failed_webhooks,
        "interval",
        minutes=2,
        id="retry_failed_webhooks",
    )
    _scheduler.start()
    log.info(
        "Scheduler started - nightly at %02d:%02d, monthly reset on 1st, "
        "container health every 15m, stuck cycle recovery every 10m, "
        "scheduled content every 5m, roadmap advance daily at 00:30, "
        "daily steering at 06:30, weekly digest Mon 09:00, "
        "webhook retry every 2m, calendar auto-fill at %02d:%02d",
        settings.nightly_hour,
        settings.nightly_minute,
        settings.nightly_hour,
        (settings.nightly_minute + 30) % 60,
    )


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler stopped")
