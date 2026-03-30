"""Test database models."""

import pytest
from sqlalchemy import select

from arclane.models.tables import Activity, Business, Content, Cycle, Metric


async def test_create_business(db_session):
    biz = Business(
        slug="test-biz",
        name="Test Business",
        description="A test business",
        owner_email="test@example.com",
    )
    db_session.add(biz)
    await db_session.commit()

    result = await db_session.execute(select(Business).where(Business.slug == "test-biz"))
    loaded = result.scalar_one()
    assert loaded.name == "Test Business"
    assert loaded.plan == "preview"
    assert loaded.working_days_remaining == 3
    assert loaded.working_days_bonus == 0


async def test_create_cycle(db_session):
    biz = Business(
        slug="cycle-test",
        name="Cycle Test",
        description="Testing cycles",
        owner_email="test@example.com",
    )
    db_session.add(biz)
    await db_session.commit()

    cycle = Cycle(business_id=biz.id, trigger="nightly", status="pending")
    db_session.add(cycle)
    await db_session.commit()

    result = await db_session.execute(select(Cycle).where(Cycle.business_id == biz.id))
    loaded = result.scalar_one()
    assert loaded.trigger == "nightly"
    assert loaded.status == "pending"


async def test_create_activity(db_session):
    biz = Business(
        slug="activity-test",
        name="Activity Test",
        description="Testing activity",
        owner_email="test@example.com",
    )
    db_session.add(biz)
    await db_session.commit()

    activity = Activity(
        business_id=biz.id,
        agent="cmo",
        action="Created blog post",
        detail="First post about the business",
    )
    db_session.add(activity)
    await db_session.commit()

    result = await db_session.execute(select(Activity).where(Activity.business_id == biz.id))
    loaded = result.scalar_one()
    assert loaded.action == "Created blog post"
    assert loaded.agent == "cmo"


async def test_create_content(db_session):
    biz = Business(
        slug="content-test",
        name="Content Test",
        description="Testing content",
        owner_email="test@example.com",
    )
    db_session.add(biz)
    await db_session.commit()

    content = Content(
        business_id=biz.id,
        content_type="blog",
        title="Hello World",
        body="This is the first post.",
        status="published",
    )
    db_session.add(content)
    await db_session.commit()

    result = await db_session.execute(select(Content).where(Content.business_id == biz.id))
    loaded = result.scalar_one()
    assert loaded.title == "Hello World"
    assert loaded.content_type == "blog"


async def test_create_metric(db_session):
    biz = Business(
        slug="metric-test",
        name="Metric Test",
        description="Testing metrics",
        owner_email="test@example.com",
    )
    db_session.add(biz)
    await db_session.commit()

    metric = Metric(business_id=biz.id, name="traffic", value=42.0)
    db_session.add(metric)
    await db_session.commit()

    result = await db_session.execute(select(Metric).where(Metric.business_id == biz.id))
    loaded = result.scalar_one()
    assert loaded.name == "traffic"
    assert loaded.value == 42.0


async def test_business_relationships(db_session):
    biz = Business(
        slug="rel-test",
        name="Relationship Test",
        description="Testing relationships",
        owner_email="test@example.com",
    )
    db_session.add(biz)
    await db_session.commit()

    cycle = Cycle(business_id=biz.id, trigger="on_demand", status="completed")
    activity = Activity(business_id=biz.id, agent="system", action="Test action")
    content = Content(business_id=biz.id, content_type="social", body="Tweet", platform="twitter")

    db_session.add_all([cycle, activity, content])
    await db_session.commit()

    from sqlalchemy.orm import selectinload

    result = await db_session.execute(
        select(Business)
        .where(Business.slug == "rel-test")
        .options(selectinload(Business.cycles), selectinload(Business.activity), selectinload(Business.content))
    )
    loaded = result.scalar_one()
    assert len(loaded.cycles) == 1
    assert len(loaded.activity) == 1
    assert len(loaded.content) == 1
