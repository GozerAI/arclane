"""Database setup."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("database")

_pool_kwargs: dict = {}
if "sqlite" not in settings.database_url:
    _pool_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
engine = create_async_engine(settings.database_url, echo=False, **_pool_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with async_session() as session:
        yield session


async def init_db():
    """Initialize database — creates tables if missing, then ensures additive columns."""
    from arclane.models.tables import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        log.info("Database tables ensured")

        await _ensure_additive_columns(conn)


async def _ensure_additive_columns(conn) -> None:
    """Add safe nullable columns that older local DBs may not have yet."""
    dialect = conn.dialect.name
    if dialect == "postgresql":
        await conn.execute(
            text("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS website_url VARCHAR(500)")
        )
        await conn.execute(
            text("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS website_summary TEXT")
        )
        return
    if dialect != "sqlite":
        return

    result = await conn.execute(text("PRAGMA table_info(businesses)"))
    columns = {row[1] for row in result.fetchall()}

    if "website_url" not in columns:
        await conn.execute(text("ALTER TABLE businesses ADD COLUMN website_url VARCHAR(500)"))
    if "website_summary" not in columns:
        await conn.execute(text("ALTER TABLE businesses ADD COLUMN website_summary TEXT"))


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
