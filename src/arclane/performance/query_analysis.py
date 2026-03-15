"""Database query plan analysis middleware — logs EXPLAIN ANALYZE for slow queries.

Item 6: Captures queries exceeding a configurable threshold (default 100ms)
and logs their execution plan for performance debugging.
"""

import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger

log = get_logger("performance.query_analysis")

# Threshold in seconds
SLOW_QUERY_THRESHOLD_S = 0.1  # 100ms


class QueryAnalyzer:
    """Tracks query execution times and logs EXPLAIN for slow queries."""

    def __init__(self, threshold_s: float = SLOW_QUERY_THRESHOLD_S):
        self._threshold = threshold_s
        self._slow_queries: list[dict] = []
        self._enabled = True

    @property
    def slow_queries(self) -> list[dict]:
        return list(self._slow_queries)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def clear(self) -> None:
        self._slow_queries.clear()

    def attach(self, engine: Engine) -> None:
        """Attach timing listeners to a SQLAlchemy engine."""
        event.listen(engine, "before_cursor_execute", self._before_execute)
        event.listen(engine, "after_cursor_execute", self._after_execute)

    def detach(self, engine: Engine) -> None:
        """Remove timing listeners from a SQLAlchemy engine."""
        event.remove(engine, "before_cursor_execute", self._before_execute)
        event.remove(engine, "after_cursor_execute", self._after_execute)

    def _before_execute(
        self, conn: Any, cursor: Any, statement: str,
        parameters: Any, context: Any, executemany: bool,
    ) -> None:
        if not self._enabled:
            return
        conn.info.setdefault("query_start_time", {})
        conn.info["query_start_time"][id(cursor)] = time.monotonic()

    def _after_execute(
        self, conn: Any, cursor: Any, statement: str,
        parameters: Any, context: Any, executemany: bool,
    ) -> None:
        if not self._enabled:
            return
        start_times = conn.info.get("query_start_time", {})
        start = start_times.pop(id(cursor), None)
        if start is None:
            return

        elapsed = time.monotonic() - start
        if elapsed >= self._threshold:
            entry = {
                "statement": statement[:2000],
                "elapsed_s": round(elapsed, 4),
                "parameters": str(parameters)[:500] if parameters else None,
            }

            # For SQLite, EXPLAIN QUERY PLAN is the equivalent of EXPLAIN ANALYZE
            explain_plan = None
            if "sqlite" in str(getattr(conn.engine, "url", "")):
                try:
                    explain_cursor = conn.connection.cursor()
                    explain_cursor.execute(f"EXPLAIN QUERY PLAN {statement}", parameters or ())
                    explain_plan = [str(row) for row in explain_cursor.fetchall()]
                    explain_cursor.close()
                except Exception:
                    pass
            else:
                try:
                    explain_cursor = conn.connection.cursor()
                    explain_cursor.execute(f"EXPLAIN ANALYZE {statement}", parameters or ())
                    explain_plan = [str(row) for row in explain_cursor.fetchall()]
                    explain_cursor.close()
                except Exception:
                    pass

            if explain_plan:
                entry["explain_plan"] = explain_plan

            self._slow_queries.append(entry)
            log.warning(
                "Slow query (%.0fms): %s",
                elapsed * 1000,
                statement[:200],
            )
            if explain_plan:
                log.info("Query plan: %s", "\n".join(explain_plan[:10]))


# Singleton instance
query_analyzer = QueryAnalyzer()
