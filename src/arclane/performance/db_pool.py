"""Test database connection pooling.

Item 245: Provides optimized database connection pooling for tests,
reusing a single engine and session factory across test cases to avoid
the overhead of creating/destroying databases per test.
"""

import asyncio
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from arclane.core.logging import get_logger

log = get_logger("performance.db_pool")


class TestDatabasePool:
    """Manages a shared database connection pool for tests.

    Creates a single in-memory SQLite engine that can be reused across
    multiple test functions, significantly reducing test setup overhead.
    """

    def __init__(self):
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None
        self._initialized = False
        self._session_count = 0

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def session_count(self) -> int:
        return self._session_count

    async def initialize(
        self,
        url: str = "sqlite+aiosqlite://",
        echo: bool = False,
    ) -> None:
        """Initialize the shared test database pool.

        Uses StaticPool for in-memory SQLite to ensure all sessions
        share the same connection (and thus the same in-memory database).
        """
        if self._initialized:
            return

        pool_class = StaticPool if "sqlite" in url else NullPool

        self._engine = create_async_engine(
            url,
            echo=echo,
            poolclass=pool_class,
            connect_args={"check_same_thread": False} if "sqlite" in url else {},
        )

        from arclane.models.tables import Base
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        self._initialized = True
        log.info("Test database pool initialized: %s", url)

    async def get_session(self) -> AsyncIterator[AsyncSession]:
        """Get a session from the pool.

        Usage:
            async for session in pool.get_session():
                await session.execute(...)
        """
        if not self._initialized or self._session_factory is None:
            await self.initialize()

        assert self._session_factory is not None
        async with self._session_factory() as session:
            self._session_count += 1
            yield session

    async def reset(self) -> None:
        """Reset all tables (truncate data, keep schema)."""
        if not self._engine:
            return

        from arclane.models.tables import Base
        async with self._engine.begin() as conn:
            # Drop and recreate for clean state
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        log.debug("Test database reset")

    async def dispose(self) -> None:
        """Dispose the engine and clean up."""
        if self._engine:
            async with self._engine.begin() as conn:
                from arclane.models.tables import Base
                await conn.run_sync(Base.metadata.drop_all)
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._initialized = False
            log.info("Test database pool disposed (sessions created: %d)", self._session_count)

    @property
    def engine(self) -> AsyncEngine | None:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker | None:
        return self._session_factory


# Singleton for test reuse
test_db_pool = TestDatabasePool()
