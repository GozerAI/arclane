"""Support ticket routes — plan-tiered SLA and dedicated support channel."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.models.tables import Business, SupportTicket

log = get_logger("support")
router = APIRouter()

SUPPORT_TIERS: dict[str, dict] = {
    "preview": {"tier": "standard", "sla_hours": 48, "can_create": False},
    "starter": {"tier": "standard", "sla_hours": 24, "can_create": True},
    "pro": {"tier": "priority", "sla_hours": 12, "can_create": True},
    "growth": {"tier": "priority", "sla_hours": 8, "can_create": True},
    "scale": {"tier": "dedicated", "sla_hours": 4, "can_create": True},
    "enterprise": {"tier": "dedicated", "sla_hours": 1, "can_create": True},
}


def _get_support_config(plan: str) -> dict:
    return SUPPORT_TIERS.get(plan, SUPPORT_TIERS["preview"])


# --- Schemas ---


class SupportTierResponse(BaseModel):
    plan: str
    tier: str
    sla_hours: int
    can_create_tickets: bool
    dedicated_channel: bool


class CreateTicketRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=500)
    body: str = Field(..., min_length=10)
    priority: str = Field(default="normal")


class TicketResponse(BaseModel):
    id: int
    subject: str
    body: str
    priority: str
    status: str
    support_tier: str
    response_sla_hours: int
    first_response_at: str | None
    resolved_at: str | None
    created_at: str


class UpdateTicketRequest(BaseModel):
    status: str | None = None
    priority: str | None = None


class DedicatedChannelResponse(BaseModel):
    channel_type: str
    contact_email: str
    sla_hours: int
    availability: str


# --- Routes ---


@router.get("/support/tier", response_model=SupportTierResponse)
async def get_support_tier(
    business: Business = Depends(get_business),
):
    """Get the support tier for this business based on its plan."""
    config = _get_support_config(business.plan)
    return SupportTierResponse(
        plan=business.plan,
        tier=config["tier"],
        sla_hours=config["sla_hours"],
        can_create_tickets=config["can_create"],
        dedicated_channel=config["tier"] == "dedicated",
    )


@router.post("/support/tickets", response_model=TicketResponse, status_code=201)
async def create_ticket(
    payload: CreateTicketRequest,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Create a support ticket. Preview plan cannot create tickets."""
    config = _get_support_config(business.plan)
    if not config["can_create"]:
        raise HTTPException(
            status_code=403,
            detail="Support tickets require a paid plan",
        )

    # Scale/enterprise auto-elevate priority
    priority = payload.priority
    if config["tier"] == "dedicated" and priority == "normal":
        priority = "high"

    ticket = SupportTicket(
        business_id=business.id,
        subject=payload.subject,
        body=payload.body,
        priority=priority,
        support_tier=config["tier"],
        response_sla_hours=config["sla_hours"],
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    log.info("Support ticket #%d created for %s (tier=%s)", ticket.id, business.slug, config["tier"])

    return TicketResponse(
        id=ticket.id, subject=ticket.subject, body=ticket.body,
        priority=ticket.priority, status=ticket.status,
        support_tier=ticket.support_tier,
        response_sla_hours=ticket.response_sla_hours,
        first_response_at=ticket.first_response_at.isoformat() if ticket.first_response_at else None,
        resolved_at=ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        created_at=ticket.created_at.isoformat(),
    )


@router.get("/support/tickets", response_model=list[TicketResponse])
async def list_tickets(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """List support tickets for this business."""
    result = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.business_id == business.id)
        .order_by(SupportTicket.created_at.desc())
    )
    tickets = result.scalars().all()

    return [
        TicketResponse(
            id=t.id, subject=t.subject, body=t.body,
            priority=t.priority, status=t.status,
            support_tier=t.support_tier,
            response_sla_hours=t.response_sla_hours,
            first_response_at=t.first_response_at.isoformat() if t.first_response_at else None,
            resolved_at=t.resolved_at.isoformat() if t.resolved_at else None,
            created_at=t.created_at.isoformat(),
        )
        for t in tickets
    ]


@router.get("/support/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: int,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get a specific support ticket."""
    result = await session.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.business_id == business.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return TicketResponse(
        id=ticket.id, subject=ticket.subject, body=ticket.body,
        priority=ticket.priority, status=ticket.status,
        support_tier=ticket.support_tier,
        response_sla_hours=ticket.response_sla_hours,
        first_response_at=ticket.first_response_at.isoformat() if ticket.first_response_at else None,
        resolved_at=ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        created_at=ticket.created_at.isoformat(),
    )


@router.patch("/support/tickets/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: int,
    payload: UpdateTicketRequest,
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Update ticket status or priority."""
    result = await session.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.business_id == business.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if payload.status:
        valid_statuses = {"open", "in_progress", "resolved", "closed"}
        if payload.status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")
        ticket.status = payload.status
        if payload.status == "resolved":
            from datetime import datetime, timezone
            ticket.resolved_at = datetime.now(timezone.utc)

    if payload.priority:
        valid_priorities = {"normal", "high", "urgent"}
        if payload.priority not in valid_priorities:
            raise HTTPException(status_code=400, detail=f"Invalid priority. Must be one of: {valid_priorities}")
        ticket.priority = payload.priority

    await session.commit()
    await session.refresh(ticket)

    return TicketResponse(
        id=ticket.id, subject=ticket.subject, body=ticket.body,
        priority=ticket.priority, status=ticket.status,
        support_tier=ticket.support_tier,
        response_sla_hours=ticket.response_sla_hours,
        first_response_at=ticket.first_response_at.isoformat() if ticket.first_response_at else None,
        resolved_at=ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        created_at=ticket.created_at.isoformat(),
    )


@router.get("/support/dedicated-channel", response_model=DedicatedChannelResponse)
async def get_dedicated_channel(
    business: Business = Depends(get_business),
):
    """Get dedicated support channel info — Scale plan and above only."""
    config = _get_support_config(business.plan)
    if config["tier"] != "dedicated":
        raise HTTPException(
            status_code=403,
            detail="Dedicated support channel requires Scale plan or above",
        )

    return DedicatedChannelResponse(
        channel_type="email",
        contact_email="dedicated@arclane.cloud",
        sla_hours=config["sla_hours"],
        availability="Business hours (9am-6pm EST, Mon-Fri) with 4-hour response SLA",
    )
