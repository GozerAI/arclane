"""Database setup."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("database")

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with async_session() as session:
        yield session


async def init_db():
    """Initialize database — uses Alembic in production, create_all in dev."""
    from arclane.models.tables import Base

    if settings.env == "production":
        log.info("Production mode — run 'alembic upgrade head' for schema migrations")
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("Development mode — tables created via create_all")


async def check_db_health() -> bool:
    """Check database connectivity."""
    try:
        async with engine.connect() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
        return True
    except Exception:
        return False
