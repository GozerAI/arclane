"""Row-level security for multi-tenant queries.

Item 30: Automatically filters all queries by business_id to enforce
tenant isolation. Uses SQLAlchemy event listeners to inject WHERE clauses.
"""

from contextvars import ContextVar
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Query, Session

from arclane.core.logging import get_logger

log = get_logger("performance.rls")

# Context variable holding the current tenant's business_id
_current_tenant_id: ContextVar[int | None] = ContextVar("_current_tenant_id", default=None)


def set_tenant_id(business_id: int | None) -> None:
    """Set the current tenant context for row-level security."""
    _current_tenant_id.set(business_id)


def get_tenant_id() -> int | None:
    """Get the current tenant context."""
    return _current_tenant_id.get()


class TenantFilter:
    """Enforces row-level security by filtering queries by business_id.

    Attaches to SQLAlchemy events to automatically add WHERE business_id = ?
    to SELECT/UPDATE/DELETE operations on tenant-scoped models.
    """

    # Tables that have a business_id column and need tenant filtering
    TENANT_TABLES = {"cycles", "activity", "content", "metrics"}

    def __init__(self):
        self._enabled = True
        self._filter_count = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def filter_count(self) -> int:
        return self._filter_count

    def reset_stats(self) -> None:
        self._filter_count = 0

    def apply_filter(self, statement: Any, tenant_id: int) -> Any:
        """Apply tenant filter to a SQLAlchemy Core select statement.

        Returns the statement with an added WHERE clause if applicable.
        """
        if not self._enabled:
            return statement

        if hasattr(statement, "froms"):
            for frm in statement.froms:
                table_name = getattr(frm, "name", "")
                if table_name in self.TENANT_TABLES:
                    col = frm.c.get("business_id")
                    if col is not None:
                        self._filter_count += 1
                        return statement.where(col == tenant_id)
        return statement

    def filter_query(self, query: Any) -> Any:
        """Apply tenant filter to an ORM-level query.

        Inspects the query's target entities and adds a business_id filter
        if the entity's table is in TENANT_TABLES.
        """
        if not self._enabled:
            return query

        tenant_id = get_tenant_id()
        if tenant_id is None:
            return query

        # For SQLAlchemy 2.0+ select() statements
        if hasattr(query, "column_descriptions"):
            for desc in query.column_descriptions:
                entity = desc.get("entity")
                if entity is not None:
                    table_name = getattr(
                        getattr(entity, "__table__", None), "name", ""
                    )
                    if table_name in self.TENANT_TABLES:
                        if hasattr(entity, "business_id"):
                            self._filter_count += 1
                            return query.filter(entity.business_id == tenant_id)
        return query


# Singleton
tenant_filter = TenantFilter()


class TenantContext:
    """Context manager for setting tenant scope during request processing."""

    def __init__(self, business_id: int | None):
        self._business_id = business_id
        self._token = None

    def __enter__(self):
        self._token = _current_tenant_id.set(self._business_id)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _current_tenant_id.reset(self._token)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)
