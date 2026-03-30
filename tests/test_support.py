"""Tests for support ticket routes and plan-tiered SLA."""

import pytest

from arclane.api.routes.support import SUPPORT_TIERS, _get_support_config
from arclane.models.tables import Business, SupportTicket


def _make_business(session, plan="scale", slug="test-biz"):
    biz = Business(
        slug=slug, name="Test", description="", owner_email="test@test.com", plan=plan,
    )
    session.add(biz)
    return biz


# --- Support tier config ---


def test_support_tier_scale_is_dedicated():
    config = _get_support_config("scale")
    assert config["tier"] == "dedicated"
    assert config["sla_hours"] == 4


def test_support_tier_enterprise_is_dedicated():
    config = _get_support_config("enterprise")
    assert config["tier"] == "dedicated"
    assert config["sla_hours"] == 1


def test_support_tier_pro_is_priority():
    config = _get_support_config("pro")
    assert config["tier"] == "priority"
    assert config["sla_hours"] == 12


def test_support_tier_growth_is_priority():
    config = _get_support_config("growth")
    assert config["tier"] == "priority"
    assert config["sla_hours"] == 8


def test_support_tier_starter_is_standard():
    config = _get_support_config("starter")
    assert config["tier"] == "standard"
    assert config["sla_hours"] == 24
    assert config["can_create"] is True


def test_support_tier_preview_cannot_create():
    config = _get_support_config("preview")
    assert config["can_create"] is False


def test_support_tier_unknown_defaults_to_preview():
    config = _get_support_config("unknown")
    assert config["tier"] == "standard"
    assert config["can_create"] is False


# --- Support tier route ---


@pytest.mark.asyncio
async def test_get_support_tier_scale(db_session):
    biz = _make_business(db_session, plan="scale")
    await db_session.commit()

    from arclane.api.routes.support import get_support_tier
    result = await get_support_tier(business=biz)
    assert result.tier == "dedicated"
    assert result.dedicated_channel is True
    assert result.sla_hours == 4


@pytest.mark.asyncio
async def test_get_support_tier_starter(db_session):
    biz = _make_business(db_session, plan="starter")
    await db_session.commit()

    from arclane.api.routes.support import get_support_tier
    result = await get_support_tier(business=biz)
    assert result.tier == "standard"
    assert result.dedicated_channel is False


# --- Create ticket ---


@pytest.mark.asyncio
async def test_create_ticket_scale_gets_4h_sla(db_session):
    biz = _make_business(db_session, plan="scale")
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.support import create_ticket, CreateTicketRequest
    payload = CreateTicketRequest(subject="Need help", body="This is a test ticket body")
    result = await create_ticket(payload=payload, business=biz, session=db_session)
    assert result.response_sla_hours == 4
    assert result.support_tier == "dedicated"
    assert result.status == "open"


@pytest.mark.asyncio
async def test_create_ticket_starter_gets_24h_sla(db_session):
    biz = _make_business(db_session, plan="starter")
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.support import create_ticket, CreateTicketRequest
    payload = CreateTicketRequest(subject="Question", body="This is a starter ticket body")
    result = await create_ticket(payload=payload, business=biz, session=db_session)
    assert result.response_sla_hours == 24
    assert result.support_tier == "standard"


@pytest.mark.asyncio
async def test_create_ticket_preview_blocked(db_session):
    from fastapi import HTTPException
    biz = _make_business(db_session, plan="preview")
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.support import create_ticket, CreateTicketRequest
    payload = CreateTicketRequest(subject="Help me", body="This should fail for preview")
    with pytest.raises(HTTPException) as exc_info:
        await create_ticket(payload=payload, business=biz, session=db_session)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_ticket_priority_auto_elevated_for_scale(db_session):
    biz = _make_business(db_session, plan="scale")
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.support import create_ticket, CreateTicketRequest
    payload = CreateTicketRequest(subject="Normal ticket", body="Should be auto-elevated to high")
    result = await create_ticket(payload=payload, business=biz, session=db_session)
    assert result.priority == "high"


# --- List tickets ---


@pytest.mark.asyncio
async def test_list_tickets_empty(db_session):
    biz = _make_business(db_session, plan="pro")
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.support import list_tickets
    result = await list_tickets(business=biz, session=db_session)
    assert result == []


@pytest.mark.asyncio
async def test_list_tickets_returns_owned_only(db_session):
    biz1 = _make_business(db_session, plan="pro", slug="biz-1")
    biz2 = _make_business(db_session, plan="pro", slug="biz-2")
    await db_session.commit()
    await db_session.refresh(biz1)
    await db_session.refresh(biz2)

    db_session.add(SupportTicket(
        business_id=biz1.id, subject="T1", body="body1",
        support_tier="priority", response_sla_hours=12,
    ))
    db_session.add(SupportTicket(
        business_id=biz2.id, subject="T2", body="body2",
        support_tier="priority", response_sla_hours=12,
    ))
    await db_session.commit()

    from arclane.api.routes.support import list_tickets
    result = await list_tickets(business=biz1, session=db_session)
    assert len(result) == 1
    assert result[0].subject == "T1"


# --- Get ticket ---


@pytest.mark.asyncio
async def test_get_ticket_detail(db_session):
    biz = _make_business(db_session, plan="pro")
    await db_session.commit()
    await db_session.refresh(biz)

    ticket = SupportTicket(
        business_id=biz.id, subject="Detail test", body="body",
        support_tier="priority", response_sla_hours=12,
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)

    from arclane.api.routes.support import get_ticket
    result = await get_ticket(ticket_id=ticket.id, business=biz, session=db_session)
    assert result.subject == "Detail test"


@pytest.mark.asyncio
async def test_get_ticket_not_found(db_session):
    from fastapi import HTTPException
    biz = _make_business(db_session, plan="pro")
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.support import get_ticket
    with pytest.raises(HTTPException) as exc_info:
        await get_ticket(ticket_id=999, business=biz, session=db_session)
    assert exc_info.value.status_code == 404


# --- Update ticket ---


@pytest.mark.asyncio
async def test_update_ticket_status(db_session):
    biz = _make_business(db_session, plan="pro")
    await db_session.commit()
    await db_session.refresh(biz)

    ticket = SupportTicket(
        business_id=biz.id, subject="Update me", body="body",
        support_tier="priority", response_sla_hours=12,
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)

    from arclane.api.routes.support import update_ticket, UpdateTicketRequest
    payload = UpdateTicketRequest(status="resolved")
    result = await update_ticket(
        ticket_id=ticket.id, payload=payload, business=biz, session=db_session,
    )
    assert result.status == "resolved"
    assert result.resolved_at is not None


# --- Dedicated channel ---


@pytest.mark.asyncio
async def test_dedicated_channel_scale_allowed(db_session):
    biz = _make_business(db_session, plan="scale")
    await db_session.commit()

    from arclane.api.routes.support import get_dedicated_channel
    result = await get_dedicated_channel(business=biz)
    assert result.channel_type == "email"
    assert result.contact_email == "dedicated@arclane.cloud"


@pytest.mark.asyncio
async def test_dedicated_channel_pro_forbidden(db_session):
    from fastapi import HTTPException
    biz = _make_business(db_session, plan="pro")
    await db_session.commit()

    from arclane.api.routes.support import get_dedicated_channel
    with pytest.raises(HTTPException) as exc_info:
        await get_dedicated_channel(business=biz)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_dedicated_channel_starter_forbidden(db_session):
    from fastapi import HTTPException
    biz = _make_business(db_session, plan="starter")
    await db_session.commit()

    from arclane.api.routes.support import get_dedicated_channel
    with pytest.raises(HTTPException) as exc_info:
        await get_dedicated_channel(business=biz)
    assert exc_info.value.status_code == 403
