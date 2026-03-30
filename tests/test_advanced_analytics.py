"""Tests for advanced analytics routes (Pro+ plan gate)."""

import pytest
from datetime import datetime, timezone

from arclane.api.routes.advanced_analytics import _require_pro_plan, ADVANCED_PLANS
from arclane.models.tables import (
    Activity,
    Business,
    BusinessHealthScore,
    Content,
    ContentPerformance,
    Cycle,
    Milestone,
    RevenueEvent,
)


def _make_business(session, plan="pro", slug="test-biz"):
    biz = Business(
        slug=slug, name="Test", description="", owner_email="test@test.com", plan=plan,
    )
    session.add(biz)
    return biz


# --- Plan gating ---


def test_advanced_plans_set():
    assert "pro" in ADVANCED_PLANS
    assert "growth" in ADVANCED_PLANS
    assert "scale" in ADVANCED_PLANS
    assert "enterprise" in ADVANCED_PLANS
    assert "starter" not in ADVANCED_PLANS
    assert "preview" not in ADVANCED_PLANS


def test_require_pro_plan_allows_pro():
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="pro")
    _require_pro_plan(biz)  # should not raise


def test_require_pro_plan_allows_growth():
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="growth")
    _require_pro_plan(biz)  # should not raise


def test_require_pro_plan_allows_scale():
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="scale")
    _require_pro_plan(biz)  # should not raise


def test_require_pro_plan_blocks_starter():
    from fastapi import HTTPException
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="starter")
    with pytest.raises(HTTPException) as exc_info:
        _require_pro_plan(biz)
    assert exc_info.value.status_code == 403


def test_require_pro_plan_blocks_preview():
    from fastapi import HTTPException
    biz = Business(slug="x", name="x", description="", owner_email="x@x.com", plan="preview")
    with pytest.raises(HTTPException) as exc_info:
        _require_pro_plan(biz)
    assert exc_info.value.status_code == 403


# --- Health trend ---


@pytest.mark.asyncio
async def test_health_trend_returns_scores(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    score = BusinessHealthScore(
        business_id=biz.id, score_type="overall", score=75.0,
        recorded_at=datetime.now(timezone.utc),
    )
    db_session.add(score)
    await db_session.commit()

    from arclane.api.routes.advanced_analytics import health_trend
    result = await health_trend(business=biz, session=db_session, days=30)
    assert len(result) == 1
    assert result[0].score == 75.0


@pytest.mark.asyncio
async def test_health_trend_empty(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.advanced_analytics import health_trend
    result = await health_trend(business=biz, session=db_session, days=30)
    assert result == []


# --- Content ROI ---


@pytest.mark.asyncio
async def test_content_roi_no_data(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.advanced_analytics import content_roi
    result = await content_roi(business=biz, session=db_session)
    assert result.total_content == 0
    assert result.total_revenue_cents == 0


@pytest.mark.asyncio
async def test_content_roi_with_data(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    c = Content(business_id=biz.id, content_type="blog", body="test", status="published")
    db_session.add(c)
    rev = RevenueEvent(
        business_id=biz.id, source="manual", amount_cents=5000,
        attribution_json={"content_id": 1},
    )
    db_session.add(rev)
    await db_session.commit()

    from arclane.api.routes.advanced_analytics import content_roi
    result = await content_roi(business=biz, session=db_session)
    assert result.total_content == 1
    assert result.total_revenue_cents == 5000


# --- Cycle efficiency ---


@pytest.mark.asyncio
async def test_cycle_efficiency_no_cycles(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.advanced_analytics import cycle_efficiency
    result = await cycle_efficiency(business=biz, session=db_session, months=3)
    assert result.total_cycles == 0
    assert result.success_rate == 0.0


@pytest.mark.asyncio
async def test_cycle_efficiency_with_cycles(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    for status in ["completed", "completed", "failed"]:
        db_session.add(Cycle(business_id=biz.id, trigger="nightly", status=status))
    await db_session.commit()

    from arclane.api.routes.advanced_analytics import cycle_efficiency
    result = await cycle_efficiency(business=biz, session=db_session, months=3)
    assert result.total_cycles == 3
    assert result.completed == 2
    assert result.failed == 1
    assert result.success_rate == pytest.approx(66.7, abs=0.1)


# --- Growth metrics ---


@pytest.mark.asyncio
async def test_growth_metrics_no_data(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.advanced_analytics import growth_metrics
    result = await growth_metrics(business=biz, session=db_session)
    assert result.content_growth_rate == 0.0
    assert result.milestone_velocity == 0.0


# --- Engagement heatmap ---


@pytest.mark.asyncio
async def test_engagement_heatmap_empty(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    from arclane.api.routes.advanced_analytics import engagement_heatmap
    result = await engagement_heatmap(business=biz, session=db_session, days=30)
    assert len(result.by_hour) == 24
    assert all(v == 0 for v in result.by_hour.values())


@pytest.mark.asyncio
async def test_engagement_heatmap_with_activity(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    for _ in range(5):
        db_session.add(Activity(
            business_id=biz.id, agent="system", action="test",
            created_at=datetime.now(timezone.utc),
        ))
    await db_session.commit()

    from arclane.api.routes.advanced_analytics import engagement_heatmap
    result = await engagement_heatmap(business=biz, session=db_session, days=30)
    assert sum(result.by_hour.values()) == 5
