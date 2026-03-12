"""Execution cycles — trigger on-demand tasks."""

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.app import limiter
from arclane.api.deps import get_business
from arclane.core.database import async_session, get_session
from arclane.core.logging import get_logger
from arclane.engine.orchestrator import orchestrator
from arclane.models.schemas import CycleRequest, CycleResponse
from arclane.models.tables import Business, Cycle

log = get_logger("cycles")
router = APIRouter()


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
    total_credits = business.credits_remaining + business.credits_bonus
    if total_credits <= 0:
        raise HTTPException(status_code=402, detail="No credits remaining")

    # Deduct from bonus first, then regular
    if business.credits_bonus > 0:
        business.credits_bonus -= 1
    else:
        business.credits_remaining -= 1

    cycle = Cycle(
        business_id=business.id,
        trigger="on_demand",
        status="pending",
        plan={"task_description": payload.task_description} if payload.task_description else None,
    )
    session.add(cycle)
    await session.commit()
    await session.refresh(cycle)

    log.info("On-demand cycle triggered for %s (cycle %d)", business.slug, cycle.id)

    # Dispatch to orchestrator in background
    background_tasks.add_task(_run_cycle, business.id, cycle.id)

    return cycle


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
    return result.scalars().all()
