"""Test configuration."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import limiter
from arclane.models.tables import Base

# Disable rate limiting during tests
limiter.enabled = False


@pytest.fixture
async def db_session():
    """In-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def mock_background_provisioning():
    """Keep tests off real provisioning and email side effects."""
    with (
        patch("arclane.api.routes.intake._provision_business_background", AsyncMock()),
        patch("arclane.api.routes.intake.send_welcome_email", AsyncMock()),
    ):
        yield
