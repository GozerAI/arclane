"""Tests for portfolio dashboard routes (Growth+ plan gate)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from arclane.api.routes.portfolio import (
    PORTFOLIO_PLANS,
    _require_portfolio_plan,
    _get_user_businesses,
)
from arclane.models.tables import Business, Content, Cycle, RevenueEvent


def _make_business(session, plan="growth", slug="biz-1", email="owner@test.com"):
    biz = Business(
        slug=slug, name=f"Business {slug}", description="",
        owner_email=email, plan=plan,
    )
    session.add(biz)
    return biz


# --- Plan gating ---


def test_portfolio_plans_set():
    assert "growth" in PORTFOLIO_PLANS
    assert "scale" in PORTFOLIO_PLANS
    assert "enterprise" in PORTFOLIO_PLANS
    assert "pro" not in PORTFOLIO_PLANS
    assert "starter" not in PORTFOLIO_PLANS


def test_require_portfolio_plan_allows_growth():
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="growth")
    _require_portfolio_plan([biz])  # should not raise


def test_require_portfolio_plan_allows_scale():
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="scale")
    _require_portfolio_plan([biz])


def test_require_portfolio_plan_blocks_pro():
    from fastapi import HTTPException
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="pro")
    with pytest.raises(HTTPException) as exc_info:
        _require_portfolio_plan([biz])
    assert exc_info.value.status_code == 403


def test_require_portfolio_plan_blocks_starter():
    from fastapi import HTTPException
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="starter")
    with pytest.raises(HTTPException) as exc_info:
        _require_portfolio_plan([biz])
    assert exc_info.value.status_code == 403


def test_require_portfolio_plan_mixed_plans():
    """If any business is on a portfolio plan, access is allowed."""
    biz1 = Business(slug="x1", name="x", description="", owner_email="x@x.com", plan="starter")
    biz2 = Business(slug="x2", name="x", description="", owner_email="x@x.com", plan="growth")
    _require_portfolio_plan([biz1, biz2])  # should not raise


def test_require_portfolio_plan_empty_list():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _require_portfolio_plan([])
    assert exc_info.value.status_code == 403


# --- get_user_businesses ---


@pytest.mark.asyncio
async def test_get_user_businesses(db_session):
    _make_business(db_session, plan="growth", slug="biz-1", email="owner@test.com")
    _make_business(db_session, plan="starter", slug="biz-2", email="owner@test.com")
    _make_business(db_session, plan="growth", slug="biz-3", email="other@test.com")
    await db_session.commit()

    businesses = await _get_user_businesses("owner@test.com", db_session)
    assert len(businesses) == 2
    slugs = {b.slug for b in businesses}
    assert slugs == {"biz-1", "biz-2"}


@pytest.mark.asyncio
async def test_get_user_businesses_excludes_cancelled(db_session):
    _make_business(db_session, plan="growth", slug="active", email="owner@test.com")
    _make_business(db_session, plan="cancelled", slug="cancelled", email="owner@test.com")
    await db_session.commit()

    businesses = await _get_user_businesses("owner@test.com", db_session)
    assert len(businesses) == 1
    assert businesses[0].slug == "active"


# --- Direct route handler tests ---


@pytest.mark.asyncio
async def test_portfolio_overview_aggregates(db_session):
    biz1 = _make_business(db_session, plan="growth", slug="biz-a", email="owner@test.com")
    biz2 = _make_business(db_session, plan="growth", slug="biz-b", email="owner@test.com")
    await db_session.commit()
    await db_session.refresh(biz1)
    await db_session.refresh(biz2)

    db_session.add(Content(business_id=biz1.id, content_type="blog", body="test"))
    db_session.add(Content(business_id=biz2.id, content_type="social", body="test"))
    db_session.add(Cycle(business_id=biz1.id, trigger="nightly", status="completed"))
    await db_session.commit()

    from arclane.api.routes.portfolio import portfolio_overview

    mock_request = MagicMock()
    with patch("arclane.api.routes.portfolio.get_current_user_email", return_value="owner@test.com"):
        result = await portfolio_overview(request=mock_request, session=db_session)

    assert result.total_businesses == 2
    assert result.total_content == 2
    assert result.total_cycles == 1


@pytest.mark.asyncio
async def test_portfolio_health(db_session):
    biz = _make_business(db_session, plan="growth", slug="health-biz")
    biz.health_score = 72.5
    await db_session.commit()

    from arclane.api.routes.portfolio import portfolio_health

    mock_request = MagicMock()
    with patch("arclane.api.routes.portfolio.get_current_user_email", return_value="owner@test.com"):
        result = await portfolio_health(request=mock_request, session=db_session)

    assert len(result) == 1
    assert result[0].health_score == 72.5


@pytest.mark.asyncio
async def test_portfolio_content_summary(db_session):
    biz = _make_business(db_session, plan="growth", slug="content-biz")
    await db_session.commit()
    await db_session.refresh(biz)

    db_session.add(Content(business_id=biz.id, content_type="blog", body="a"))
    db_session.add(Content(business_id=biz.id, content_type="blog", body="b"))
    db_session.add(Content(business_id=biz.id, content_type="social", body="c"))
    await db_session.commit()

    from arclane.api.routes.portfolio import portfolio_content_summary

    mock_request = MagicMock()
    with patch("arclane.api.routes.portfolio.get_current_user_email", return_value="owner@test.com"):
        result = await portfolio_content_summary(request=mock_request, session=db_session)

    assert result.total_content == 3
    assert result.by_type["blog"] == 2
    assert result.by_type["social"] == 1


@pytest.mark.asyncio
async def test_portfolio_revenue_summary(db_session):
    biz = _make_business(db_session, plan="scale", slug="rev-biz")
    await db_session.commit()
    await db_session.refresh(biz)

    db_session.add(RevenueEvent(business_id=biz.id, source="stripe", amount_cents=10000))
    db_session.add(RevenueEvent(business_id=biz.id, source="gumroad", amount_cents=5000))
    await db_session.commit()

    from arclane.api.routes.portfolio import portfolio_revenue_summary

    mock_request = MagicMock()
    with patch("arclane.api.routes.portfolio.get_current_user_email", return_value="owner@test.com"):
        result = await portfolio_revenue_summary(request=mock_request, session=db_session)

    assert result.total_revenue_cents == 15000
    assert result.by_source["stripe"] == 10000


@pytest.mark.asyncio
async def test_portfolio_cycle_status(db_session):
    biz = _make_business(db_session, plan="growth", slug="cycle-biz")
    await db_session.commit()
    await db_session.refresh(biz)

    db_session.add(Cycle(business_id=biz.id, trigger="nightly", status="completed"))
    db_session.add(Cycle(business_id=biz.id, trigger="nightly", status="failed"))
    await db_session.commit()

    from arclane.api.routes.portfolio import portfolio_cycle_status

    mock_request = MagicMock()
    with patch("arclane.api.routes.portfolio.get_current_user_email", return_value="owner@test.com"):
        result = await portfolio_cycle_status(request=mock_request, session=db_session)

    assert len(result) == 1
    assert result[0].total_cycles == 2


@pytest.mark.asyncio
async def test_portfolio_no_businesses(db_session):
    from arclane.api.routes.portfolio import _get_user_businesses
    businesses = await _get_user_businesses("nobody@test.com", db_session)
    assert businesses == []
