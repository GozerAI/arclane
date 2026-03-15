"""Execution cycles — trigger on-demand tasks."""

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.app import limiter
from arclane.api.deps import get_business
from arclane.core.database import async_session, get_session
from arclane.core.logging import get_logger
from arclane.engine.operating_plan import reserve_included_cycle
from arclane.engine.orchestrator import orchestrator
from arclane.models.schemas import CycleRequest, CycleResponse
from arclane.models.tables import Activity, Business, Cycle

log = get_logger("cycles")
router = APIRouter()


def _serialize_cycle(cycle: Cycle) -> CycleResponse:
    result = cycle.result or {}
    total_tasks = result.get("total")
    failed_tasks = result.get("failed")
    return CycleResponse(
        id=cycle.id,
        trigger=cycle.trigger,
        status=cycle.status,
        created_at=cycle.created_at,
        started_at=cycle.started_at,
        completed_at=cycle.completed_at,
        total_tasks=total_tasks,
        failed_tasks=failed_tasks,
    )


async def _run_cycle(business_id: int, cycle_id: int):
    """Background task to execute a cycle."""
    async with async_session() as session:
        business = await session.get(Business, business_id)
        cycle = await session.get(Cycle, cycle_id)
        if not business or not cycle:
            log.error("Cycle dispatch: business %d or cycle %d not found", business_id, cycle_id)
            return
        try:
            await orchestrator.execute_cycle(business, cycle, session)
        except Exception:
            log.exception("Cycle %d failed for %s", cycle_id, business.slug)
            cycle.status = "failed"
            await session.commit()


@router.post("", response_model=CycleResponse, status_code=201)
@limiter.limit("10/minute")
async def trigger_cycle(
    request: Request,
    payload: CycleRequest,
    background_tasks: BackgroundTasks,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    used_included_cycle = False
    if not payload.task_description:
        next_task = orchestrator.next_queue_task(business)
        if next_task and next_task.get("kind") == "add_on":
            operating_plan = (business.agent_config or {}).get("operating_plan") or {}
            updated_plan, used_included_cycle = reserve_included_cycle(
                operating_plan,
                next_task.get("queue_task_key") or next_task.get("key", ""),
            )
            if used_included_cycle:
                updated_agent_config = dict(business.agent_config or {})
                updated_agent_config["operating_plan"] = updated_plan
                business.agent_config = updated_agent_config

    if not used_included_cycle:
        # Atomic credit deduction - deduct bonus first, then regular.
        # Uses SQL-level decrement with a WHERE guard to prevent race conditions.
        result = await session.execute(
            update(Business)
            .where(Business.id == business.id, Business.credits_bonus > 0)
            .values(credits_bonus=Business.credits_bonus - 1)
        )
        if result.rowcount == 0:
            result = await session.execute(
                update(Business)
                .where(Business.id == business.id, Business.credits_remaining > 0)
                .values(credits_remaining=Business.credits_remaining - 1)
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=402, detail="No credits remaining")

    cycle = Cycle(
        business_id=business.id,
        trigger="on_demand",
        status="pending",
        plan={"task_description": payload.task_description} if payload.task_description else None,
    )
    session.add(cycle)
    await session.commit()
    await session.refresh(cycle)
    # Refresh business to reflect the atomic deduction
    await session.refresh(business)

    session.add(
        Activity(
            business_id=business.id,
            cycle_id=cycle.id,
            agent="system",
            action="Task queued",
            detail=(
                payload.task_description
                or ("Add-on cycle queued without reducing your current balance" if used_included_cycle else "Autonomous cycle queued")
            )[:300],
        )
    )
    await session.commit()

    log.info("On-demand cycle triggered for %s (cycle %d)", business.slug, cycle.id)

    # Dispatch to orchestrator in background
    background_tasks.add_task(_run_cycle, business.id, cycle.id)

    return _serialize_cycle(cycle)


@router.get("", response_model=list[CycleResponse])
async def list_cycles(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Cycle)
        .where(Cycle.business_id == business.id)
        .order_by(Cycle.created_at.desc())
        .limit(50)
    )
    return [_serialize_cycle(cycle) for cycle in result.scalars().all()]
