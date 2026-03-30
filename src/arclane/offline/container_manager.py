"""Offline container management without Docker API dependency.

Item 782: Manages container state tracking, health monitoring, and lifecycle
operations using local state files when the Docker daemon is unreachable.
Queues operations for replay when Docker becomes available.
"""

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class ContainerState(Enum):
    """Tracked state of a container."""
    UNKNOWN = "unknown"
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    PENDING_CREATE = "pending_create"
    PENDING_START = "pending_start"
    PENDING_STOP = "pending_stop"
    PENDING_REMOVE = "pending_remove"


class ContainerAction(Enum):
    """Actions that can be queued for deferred execution."""
    CREATE = "create"
    START = "start"
    STOP = "stop"
    REMOVE = "remove"
    RESTART = "restart"
    HEALTH_CHECK = "health_check"


@dataclass
class ContainerRecord:
    """Local record of a container's state and config."""

    container_id: str = ""
    business_slug: str = ""
    template: str = ""
    state: ContainerState = ContainerState.UNKNOWN
    port: Optional[int] = None
    image: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    health_status: str = "unknown"
    last_health_check: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "container_id": self.container_id,
            "business_slug": self.business_slug,
            "template": self.template,
            "state": self.state.value,
            "port": self.port,
            "image": self.image,
            "health_status": self.health_status,
            "last_health_check": self.last_health_check.isoformat() if self.last_health_check else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class QueuedAction:
    """A container action queued for deferred execution."""

    id: str = field(default_factory=lambda: str(uuid4()))
    business_slug: str = ""
    action: ContainerAction = ContainerAction.CREATE
    params: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | completed | failed
    error: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "business_slug": self.business_slug,
            "action": self.action.value,
            "params": self.params,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


class OfflineContainerManager:
    """Manages container state and queues operations when Docker is unavailable.

    All state is persisted to SQLite so it survives restarts. When Docker becomes
    available, queued actions can be replayed in order.
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            data_dir = Path(__file__).parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "containers.db"
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS containers (
                        container_id TEXT PRIMARY KEY,
                        business_slug TEXT NOT NULL UNIQUE,
                        template TEXT DEFAULT '',
                        state TEXT DEFAULT 'unknown',
                        port INTEGER,
                        image TEXT DEFAULT '',
                        config TEXT DEFAULT '{}',
                        health_status TEXT DEFAULT 'unknown',
                        last_health_check TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_containers_slug ON containers(business_slug)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS action_queue (
                        id TEXT PRIMARY KEY,
                        business_slug TEXT NOT NULL,
                        action TEXT NOT NULL,
                        params TEXT DEFAULT '{}',
                        status TEXT DEFAULT 'pending',
                        error TEXT DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_action_queue_status ON action_queue(status)
                """)

    # -- Container state tracking --

    def register_container(
        self,
        business_slug: str,
        template: str = "",
        port: Optional[int] = None,
        image: str = "",
        config: Optional[Dict[str, Any]] = None,
        container_id: Optional[str] = None,
    ) -> ContainerRecord:
        """Register or update a container in the local state store."""
        cid = container_id or f"offline-{business_slug}-{uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        record = ContainerRecord(
            container_id=cid,
            business_slug=business_slug,
            template=template,
            state=ContainerState.CREATED,
            port=port,
            image=image,
            config=config or {},
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO containers
                    (container_id, business_slug, template, state, port, image, config,
                     health_status, last_health_check, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.container_id, record.business_slug, record.template,
                    record.state.value, record.port, record.image,
                    json.dumps(record.config), record.health_status, None,
                    record.created_at.isoformat(), record.updated_at.isoformat(),
                ))
        logger.info("Registered container %s for %s", cid, business_slug)
        return record

    def get_container(self, business_slug: str) -> Optional[ContainerRecord]:
        """Get the container record for a business."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM containers WHERE business_slug = ?", (business_slug,)
                ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_all_containers(self) -> List[ContainerRecord]:
        """Get all registered containers."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM containers").fetchall()
        return [self._row_to_record(r) for r in rows]

    def update_state(self, business_slug: str, state: ContainerState) -> bool:
        """Update a container's state."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cursor = conn.execute(
                    "UPDATE containers SET state = ?, updated_at = ? WHERE business_slug = ?",
                    (state.value, now, business_slug),
                )
                return cursor.rowcount > 0

    def update_health(self, business_slug: str, health_status: str) -> bool:
        """Update a container's health status."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cursor = conn.execute(
                    "UPDATE containers SET health_status = ?, last_health_check = ?, updated_at = ? WHERE business_slug = ?",
                    (health_status, now, now, business_slug),
                )
                return cursor.rowcount > 0

    def remove_container(self, business_slug: str) -> bool:
        """Remove a container record."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cursor = conn.execute(
                    "DELETE FROM containers WHERE business_slug = ?", (business_slug,)
                )
                return cursor.rowcount > 0

    # -- Action queue --

    def queue_action(
        self,
        business_slug: str,
        action: ContainerAction,
        params: Optional[Dict[str, Any]] = None,
    ) -> QueuedAction:
        """Queue a container action for deferred execution."""
        now = datetime.now(timezone.utc)
        queued = QueuedAction(
            business_slug=business_slug,
            action=action,
            params=params or {},
            created_at=now,
        )
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("""
                    INSERT INTO action_queue (id, business_slug, action, params, status, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    queued.id, queued.business_slug, queued.action.value,
                    json.dumps(queued.params), queued.status, queued.error,
                    queued.created_at.isoformat(),
                ))

        # Update container state to reflect pending action
        pending_state_map = {
            ContainerAction.CREATE: ContainerState.PENDING_CREATE,
            ContainerAction.START: ContainerState.PENDING_START,
            ContainerAction.STOP: ContainerState.PENDING_STOP,
            ContainerAction.REMOVE: ContainerState.PENDING_REMOVE,
        }
        if action in pending_state_map:
            self.update_state(business_slug, pending_state_map[action])

        logger.info("Queued %s action for %s", action.value, business_slug)
        return queued

    def get_pending_actions(self, business_slug: Optional[str] = None) -> List[QueuedAction]:
        """Get pending actions, optionally filtered by business."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                if business_slug:
                    rows = conn.execute(
                        "SELECT * FROM action_queue WHERE status = 'pending' AND business_slug = ? ORDER BY created_at ASC",
                        (business_slug,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM action_queue WHERE status = 'pending' ORDER BY created_at ASC"
                    ).fetchall()
        return [self._row_to_action(r) for r in rows]

    def complete_action(self, action_id: str) -> bool:
        """Mark a queued action as completed."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cursor = conn.execute(
                    "UPDATE action_queue SET status = 'completed' WHERE id = ?", (action_id,)
                )
                return cursor.rowcount > 0

    def fail_action(self, action_id: str, error: str = "") -> bool:
        """Mark a queued action as failed."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cursor = conn.execute(
                    "UPDATE action_queue SET status = 'failed', error = ? WHERE id = ?",
                    (error[:500], action_id),
                )
                return cursor.rowcount > 0

    def replay_actions(self, handler) -> Dict[str, int]:
        """Replay pending actions through a handler.

        The handler receives (business_slug, action, params) and returns True on success.
        Returns counts of {completed, failed}.
        """
        pending = self.get_pending_actions()
        completed = 0
        failed = 0

        for queued in pending:
            try:
                success = handler(queued.business_slug, queued.action, queued.params)
                if success:
                    self.complete_action(queued.id)
                    completed += 1
                else:
                    self.fail_action(queued.id, "handler returned False")
                    failed += 1
            except Exception as exc:
                self.fail_action(queued.id, str(exc)[:500])
                failed += 1
                logger.warning("Action replay failed for %s: %s", queued.id, exc)

        return {"completed": completed, "failed": failed}

    def clear_completed(self) -> int:
        """Remove completed actions from the queue."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                cursor = conn.execute("DELETE FROM action_queue WHERE status = 'completed'")
                return cursor.rowcount

    # -- Health monitoring --

    def check_health_all(self) -> List[Dict[str, Any]]:
        """Run health checks on all containers. In offline mode, marks stale containers.

        Returns a list of health check results.
        """
        containers = self.get_all_containers()
        results = []
        now = datetime.now(timezone.utc)

        for container in containers:
            health = "unknown"
            if container.state == ContainerState.RUNNING:
                # If last health check is stale (>15 min), mark degraded
                if container.last_health_check:
                    elapsed = (now - container.last_health_check).total_seconds()
                    if elapsed < 900:  # 15 min
                        health = "healthy"
                    elif elapsed < 3600:  # 1 hour
                        health = "degraded"
                    else:
                        health = "stale"
                else:
                    health = "unknown"
            elif container.state == ContainerState.STOPPED:
                health = "stopped"
            elif container.state in (
                ContainerState.PENDING_CREATE,
                ContainerState.PENDING_START,
            ):
                health = "pending"
            elif container.state == ContainerState.FAILED:
                health = "failed"

            self.update_health(container.business_slug, health)
            results.append({
                "business_slug": container.business_slug,
                "container_id": container.container_id,
                "state": container.state.value,
                "health": health,
            })

        return results

    def stats(self) -> Dict[str, Any]:
        """Return container management statistics."""
        containers = self.get_all_containers()
        pending_actions = self.get_pending_actions()

        state_counts: Dict[str, int] = {}
        for c in containers:
            state_counts[c.state.value] = state_counts.get(c.state.value, 0) + 1

        return {
            "total_containers": len(containers),
            "state_distribution": state_counts,
            "pending_actions": len(pending_actions),
        }

    # -- Private --

    def _row_to_record(self, row: sqlite3.Row) -> ContainerRecord:
        def _parse_dt(val):
            if not val:
                return None
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return ContainerRecord(
            container_id=row["container_id"],
            business_slug=row["business_slug"],
            template=row["template"] or "",
            state=ContainerState(row["state"]) if row["state"] else ContainerState.UNKNOWN,
            port=row["port"],
            image=row["image"] or "",
            config=json.loads(row["config"] or "{}"),
            health_status=row["health_status"] or "unknown",
            last_health_check=_parse_dt(row["last_health_check"]),
            created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
            updated_at=_parse_dt(row["updated_at"]) or datetime.now(timezone.utc),
        )

    def _row_to_action(self, row: sqlite3.Row) -> QueuedAction:
        def _parse_dt(val):
            if not val:
                return datetime.now(timezone.utc)
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return QueuedAction(
            id=row["id"],
            business_slug=row["business_slug"],
            action=ContainerAction(row["action"]),
            params=json.loads(row["params"] or "{}"),
            status=row["status"] or "pending",
            error=row["error"] or "",
            created_at=_parse_dt(row["created_at"]),
        )
