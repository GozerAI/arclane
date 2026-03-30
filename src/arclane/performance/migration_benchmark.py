"""Database migration performance benchmarking.

Item 23: Measures execution time and impact of Alembic migrations,
providing before/after comparison of table counts and schema changes.
"""

import asyncio
import inspect as _inspect
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from arclane.core.logging import get_logger

log = get_logger("performance.migration_benchmark")


@dataclass
class MigrationBenchmark:
    """Result of a migration benchmark run."""
    revision: str
    direction: str  # "upgrade" or "downgrade"
    elapsed_s: float = 0.0
    tables_before: list[str] = field(default_factory=list)
    tables_after: list[str] = field(default_factory=list)
    tables_added: list[str] = field(default_factory=list)
    tables_removed: list[str] = field(default_factory=list)
    row_counts_before: dict[str, int] = field(default_factory=dict)
    row_counts_after: dict[str, int] = field(default_factory=dict)
    success: bool = True
    error: str | None = None


class MigrationBenchmarker:
    """Benchmarks Alembic migration performance."""

    def __init__(self):
        self._results: list[MigrationBenchmark] = []

    @property
    def results(self) -> list[MigrationBenchmark]:
        return list(self._results)

    def clear(self) -> None:
        self._results.clear()

    async def snapshot_schema(self, engine: AsyncEngine) -> tuple[list[str], dict[str, int]]:
        """Capture current table names and row counts."""
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
            row_counts: dict[str, int] = {}
            for table in tables:
                try:
                    result = await conn.execute(text(f"SELECT COUNT(*) FROM \"{table}\""))
                    row_counts[table] = result.scalar() or 0
                except Exception:
                    row_counts[table] = -1
        return sorted(tables), row_counts

    async def benchmark_migration(
        self,
        engine: AsyncEngine,
        revision: str,
        direction: str,
        run_migration: Any,
    ) -> MigrationBenchmark:
        """Run a migration and benchmark its performance.

        Args:
            engine: AsyncEngine to inspect.
            revision: Alembic revision identifier.
            direction: "upgrade" or "downgrade".
            run_migration: Callable that performs the actual migration.

        Returns:
            MigrationBenchmark with timing and schema diff.
        """
        benchmark = MigrationBenchmark(revision=revision, direction=direction)

        # Before snapshot
        tables_before, counts_before = await self.snapshot_schema(engine)
        benchmark.tables_before = tables_before
        benchmark.row_counts_before = counts_before

        # Run migration
        start = time.monotonic()
        try:
            if _inspect.iscoroutinefunction(run_migration):
                await run_migration()
            else:
                run_migration()
        except Exception as exc:
            benchmark.success = False
            benchmark.error = str(exc)
            benchmark.elapsed_s = time.monotonic() - start
            self._results.append(benchmark)
            return benchmark

        benchmark.elapsed_s = time.monotonic() - start

        # After snapshot
        tables_after, counts_after = await self.snapshot_schema(engine)
        benchmark.tables_after = tables_after
        benchmark.row_counts_after = counts_after

        # Compute diffs
        before_set = set(tables_before)
        after_set = set(tables_after)
        benchmark.tables_added = sorted(after_set - before_set)
        benchmark.tables_removed = sorted(before_set - after_set)

        self._results.append(benchmark)

        log.info(
            "Migration %s %s completed in %.3fs — tables: +%d/-%d",
            direction, revision, benchmark.elapsed_s,
            len(benchmark.tables_added), len(benchmark.tables_removed),
        )

        return benchmark

    def summary(self) -> dict:
        """Produce a summary of all benchmark results."""
        if not self._results:
            return {"total": 0, "results": []}

        total_time = sum(r.elapsed_s for r in self._results)
        return {
            "total": len(self._results),
            "total_elapsed_s": round(total_time, 4),
            "avg_elapsed_s": round(total_time / len(self._results), 4),
            "failures": sum(1 for r in self._results if not r.success),
            "results": [
                {
                    "revision": r.revision,
                    "direction": r.direction,
                    "elapsed_s": r.elapsed_s,
                    "tables_added": r.tables_added,
                    "tables_removed": r.tables_removed,
                    "success": r.success,
                    "error": r.error,
                }
                for r in self._results
            ],
        }


# Singleton
migration_benchmarker = MigrationBenchmarker()
