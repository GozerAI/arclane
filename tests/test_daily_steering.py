"""Tests for daily steering interaction."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from arclane.models.tables import Business, Content, Cycle, Milestone
from arclane.services.daily_steering import generate_steering_brief


def _make_business(session, slug="test-biz", phase=1, day=5):
    biz = Business(
        slug=slug, name="Test Biz", description="", owner_email="founder@test.com",
        plan="pro", current_phase=phase, roadmap_day=day, health_score=72.0,
    )
    session.add(biz)
    return biz


@pytest.mark.asyncio
async def test_steering_brief_no_cycles(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    brief = await generate_steering_brief(biz, db_session)
    assert brief["day"] == 5
    assert brief["phase"] == "Foundation"
    assert "No cycles" in brief["last_cycle_summary"]
    assert brief["steering_prompt"]


@pytest.mark.asyncio
async def test_steering_brief_with_cycle(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    cycle = Cycle(
        business_id=biz.id, trigger="nightly", status="completed",
        result={"tasks": [{"name": "t1"}, {"name": "t2"}]},
    )
    db_session.add(cycle)
    await db_session.commit()

    brief = await generate_steering_brief(biz, db_session)
    assert "completed" in brief["last_cycle_summary"]
    assert "2 tasks" in brief["last_cycle_summary"]


@pytest.mark.asyncio
async def test_steering_brief_with_content(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    db_session.add(Content(
        business_id=biz.id, content_type="blog", title="Market Analysis",
        body="content", status="draft",
    ))
    await db_session.commit()

    brief = await generate_steering_brief(biz, db_session)
    assert len(brief["content_produced"]) == 1
    assert brief["content_produced"][0]["title"] == "Market Analysis"


@pytest.mark.asyncio
async def test_steering_brief_with_milestones(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    db_session.add(Milestone(
        business_id=biz.id, phase_number=1, key="strategy-brief",
        title="Strategy Brief", category="deliverable", status="completed",
        completed_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    brief = await generate_steering_brief(biz, db_session)
    assert len(brief["milestones_hit"]) == 1
    assert brief["milestones_hit"][0]["title"] == "Strategy Brief"


@pytest.mark.asyncio
async def test_steering_brief_with_pending_milestones(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    db_session.add(Milestone(
        business_id=biz.id, phase_number=1, key="landing-page",
        title="Landing Page Draft", category="deliverable",
        status="pending", due_day=6,
    ))
    await db_session.commit()

    brief = await generate_steering_brief(biz, db_session)
    assert len(brief["today_plan"]) == 1
    assert "Landing Page Draft" in brief["today_plan_text"]


@pytest.mark.asyncio
async def test_steering_brief_phase_names(db_session):
    for phase, expected in [(1, "Foundation"), (2, "Validation"), (3, "Growth"), (4, "Scale-Ready")]:
        biz = _make_business(db_session, slug=f"biz-{phase}", phase=phase)
        await db_session.commit()
        await db_session.refresh(biz)
        brief = await generate_steering_brief(biz, db_session)
        assert brief["phase"] == expected


@pytest.mark.asyncio
async def test_steering_brief_health_score(db_session):
    biz = _make_business(db_session)
    biz.health_score = 85.5
    await db_session.commit()
    await db_session.refresh(biz)

    brief = await generate_steering_brief(biz, db_session)
    assert brief["health_score"] == 85.5


@pytest.mark.asyncio
async def test_steering_brief_no_pending_milestones(db_session):
    biz = _make_business(db_session)
    await db_session.commit()
    await db_session.refresh(biz)

    brief = await generate_steering_brief(biz, db_session)
    assert "health scores" in brief["today_plan_text"]


# --- Telegram ---


@pytest.mark.asyncio
async def test_telegram_steering_no_token():
    from arclane.services.telegram_steering import send_steering_telegram
    with patch("arclane.services.telegram_steering.settings") as s:
        s.telegram_bot_token = ""
        result = await send_steering_telegram("12345", {"day": 1, "phase": "Foundation"})
        assert result is False


@pytest.mark.asyncio
async def test_telegram_steering_sends():
    from arclane.services.telegram_steering import send_steering_telegram

    brief = {
        "day": 5, "phase": "Foundation",
        "last_cycle_summary": "Cycle completed, 3 tasks done.",
        "content_produced": [{"title": "Blog Post", "type": "blog"}],
        "milestones_hit": [],
        "health_score": 72.0,
        "today_plan_text": "Next up: Landing Page Draft",
        "steering_prompt": "Reply with direction.",
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with (
        patch("arclane.services.telegram_steering.settings") as s,
        patch("arclane.services.telegram_steering.httpx.AsyncClient") as mock_client_cls,
    ):
        s.telegram_bot_token = "test-token"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await send_steering_telegram("12345", brief)
        assert result is True
        mock_client.post.assert_called_once()


# --- Email ---


@pytest.mark.asyncio
async def test_steering_email_function_exists():
    """Verify the email function is importable."""
    from arclane.notifications import send_daily_steering_email
    assert callable(send_daily_steering_email)


# --- Scheduler integration ---


def test_daily_steering_scheduler_job():
    """Verify the daily_steering job exists in scheduler setup."""
    import inspect
    from arclane.engine.scheduler import start_scheduler
    source = inspect.getsource(start_scheduler)
    assert "daily_steering" in source
    assert "_send_daily_steering" in source
