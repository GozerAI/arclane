"""Template version management -- auto-version and migrate templates on schema changes.

Item 934: Tracks template schema versions, detects when templates have changed,
generates migration steps, and applies them to deployed instances. All state
is persisted locally so versioning works offline.
"""

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class TemplateVersion:
    """A specific version of a template schema."""

    version_id: str = field(default_factory=lambda: str(uuid4()))
    template_name: str = ""
    version: int = 1
    schema_hash: str = ""
    files: Dict[str, str] = field(default_factory=dict)
    required_vars: List[str] = field(default_factory=list)
    optional_vars: List[str] = field(default_factory=list)
    changelog: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "template_name": self.template_name,
            "version": self.version,
            "schema_hash": self.schema_hash,
            "file_count": len(self.files),
            "files": list(self.files.keys()),
            "required_vars": self.required_vars,
            "optional_vars": self.optional_vars,
            "changelog": self.changelog,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class SchemaMigration:
    """A migration step between two template versions."""

    migration_id: str = field(default_factory=lambda: str(uuid4()))
    template_name: str = ""
    from_version: int = 0
    to_version: int = 0
    changes: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # pending | applied | failed
    applied_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "template_name": self.template_name,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changes": self.changes,
            "change_count": self.change_count,
            "status": self.status,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "created_at": self.created_at.isoformat(),
        }


class TemplateVersionManager:
    """Manages template schema versioning and migrations.

    Detects when template schemas change (files added/removed/modified,
    variables changed), auto-increments versions, and generates migration
    steps that can be applied to deployed business instances.
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            data_dir = Path(__file__).parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "template_versions.db"
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS versions (
                        version_id TEXT PRIMARY KEY,
                        template_name TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        schema_hash TEXT NOT NULL,
                        files TEXT DEFAULT '{}',
                        required_vars TEXT DEFAULT '[]',
                        optional_vars TEXT DEFAULT '[]',
                        changelog TEXT DEFAULT '',
                        created_at TEXT NOT NULL,
                        UNIQUE(template_name, version)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_versions_template ON versions(template_name)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS migrations (
                        migration_id TEXT PRIMARY KEY,
                        template_name TEXT NOT NULL,
                        from_version INTEGER NOT NULL,
                        to_version INTEGER NOT NULL,
                        changes TEXT DEFAULT '[]',
                        status TEXT DEFAULT 'pending',
                        applied_at TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_migrations_template ON migrations(template_name)
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS deployed (
                        business_slug TEXT PRIMARY KEY,
                        template_name TEXT NOT NULL,
                        current_version INTEGER NOT NULL DEFAULT 1,
                        updated_at TEXT NOT NULL
                    )
                """)

    def register_version(
        self,
        template_name: str,
        files: Dict[str, str],
        required_vars: Optional[List[str]] = None,
        optional_vars: Optional[List[str]] = None,
        changelog: str = "",
    ) -> Tuple[TemplateVersion, Optional[SchemaMigration]]:
        """Register a template version. Auto-increments if schema has changed.

        Returns (version, migration_or_None). If the schema hash matches
        the current latest version, returns the existing version with no migration.
        """
        schema_hash = self._compute_hash(files, required_vars or [], optional_vars or [])
        latest = self.get_latest_version(template_name)

        if latest and latest.schema_hash == schema_hash:
            return latest, None

        new_version_num = (latest.version + 1) if latest else 1
        now = datetime.now(timezone.utc)

        version = TemplateVersion(
            template_name=template_name,
            version=new_version_num,
            schema_hash=schema_hash,
            files=files,
            required_vars=required_vars or [],
            optional_vars=optional_vars or [],
            changelog=changelog,
            created_at=now,
        )

        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("""
                    INSERT INTO versions (version_id, template_name, version, schema_hash,
                        files, required_vars, optional_vars, changelog, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    version.version_id, version.template_name, version.version,
                    version.schema_hash, json.dumps(version.files),
                    json.dumps(version.required_vars), json.dumps(version.optional_vars),
                    version.changelog, version.created_at.isoformat(),
                ))

        migration = None
        if latest:
            changes = self._compute_changes(latest, version)
            migration = SchemaMigration(
                template_name=template_name,
                from_version=latest.version,
                to_version=version.version,
                changes=changes,
                created_at=now,
            )
            with self._lock:
                with sqlite3.connect(str(self.db_path)) as conn:
                    conn.execute("""
                        INSERT INTO migrations (migration_id, template_name, from_version,
                            to_version, changes, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        migration.migration_id, migration.template_name,
                        migration.from_version, migration.to_version,
                        json.dumps(migration.changes), migration.status,
                        migration.created_at.isoformat(),
                    ))

        logger.info(
            "Registered template %s version %d (hash=%s)",
            template_name, version.version, schema_hash[:12],
        )
        return version, migration

    def get_latest_version(self, template_name: str) -> Optional[TemplateVersion]:
        """Get the latest version of a template."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM versions WHERE template_name = ? ORDER BY version DESC LIMIT 1",
                    (template_name,),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_version(row)

    def get_version(self, template_name: str, version: int) -> Optional[TemplateVersion]:
        """Get a specific version of a template."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM versions WHERE template_name = ? AND version = ?",
                    (template_name, version),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_version(row)

    def list_versions(self, template_name: str) -> List[TemplateVersion]:
        """List all versions of a template in ascending order."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM versions WHERE template_name = ? ORDER BY version ASC",
                    (template_name,),
                ).fetchall()
        return [self._row_to_version(r) for r in rows]

    def get_migrations(self, template_name: str) -> List[SchemaMigration]:
        """Get all migrations for a template."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM migrations WHERE template_name = ? ORDER BY from_version ASC",
                    (template_name,),
                ).fetchall()
        return [self._row_to_migration(r) for r in rows]

    def get_pending_migrations(self, template_name: str) -> List[SchemaMigration]:
        """Get pending migrations for a template."""
        return [m for m in self.get_migrations(template_name) if m.status == "pending"]

    def register_deployment(self, business_slug: str, template_name: str, version: int) -> None:
        """Register that a business is deployed at a specific template version."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO deployed (business_slug, template_name, current_version, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (business_slug, template_name, version, now))

    def get_deployment_version(self, business_slug: str) -> Optional[Dict[str, Any]]:
        """Get the deployed template version for a business."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM deployed WHERE business_slug = ?", (business_slug,)
                ).fetchone()
        if row is None:
            return None
        return {
            "business_slug": row["business_slug"],
            "template_name": row["template_name"],
            "current_version": row["current_version"],
            "updated_at": row["updated_at"],
        }

    def get_outdated_deployments(self, template_name: str) -> List[Dict[str, Any]]:
        """Find deployments that are behind the latest template version."""
        latest = self.get_latest_version(template_name)
        if latest is None:
            return []

        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM deployed WHERE template_name = ? AND current_version < ?",
                    (template_name, latest.version),
                ).fetchall()

        return [
            {
                "business_slug": row["business_slug"],
                "current_version": row["current_version"],
                "latest_version": latest.version,
                "versions_behind": latest.version - row["current_version"],
            }
            for row in rows
        ]

    def apply_migration(
        self,
        migration_id: str,
        business_slug: str,
        apply_fn: Optional[Callable[[SchemaMigration, str], bool]] = None,
    ) -> bool:
        """Apply a migration to a business deployment.

        If apply_fn is provided, it receives (migration, business_slug) and should
        return True on success. Otherwise, the migration is just marked as applied.
        """
        migration = self._get_migration(migration_id)
        if migration is None:
            return False

        if apply_fn:
            try:
                success = apply_fn(migration, business_slug)
                if not success:
                    self._update_migration_status(migration_id, "failed")
                    return False
            except Exception as exc:
                logger.error("Migration %s failed for %s: %s", migration_id, business_slug, exc)
                self._update_migration_status(migration_id, "failed")
                return False

        now = datetime.now(timezone.utc)
        self._update_migration_status(migration_id, "applied", applied_at=now)
        self.register_deployment(business_slug, migration.template_name, migration.to_version)
        return True

    def needs_migration(self, business_slug: str) -> bool:
        """Check if a deployment needs migration to the latest version."""
        deployment = self.get_deployment_version(business_slug)
        if deployment is None:
            return False
        latest = self.get_latest_version(deployment["template_name"])
        if latest is None:
            return False
        return deployment["current_version"] < latest.version

    def migration_path(self, template_name: str, from_version: int) -> List[SchemaMigration]:
        """Get the ordered list of migrations needed to go from a version to latest."""
        all_migrations = self.get_migrations(template_name)
        path = []
        current = from_version
        for m in all_migrations:
            if m.from_version == current:
                path.append(m)
                current = m.to_version
        return path

    def stats(self) -> Dict[str, Any]:
        """Return version management statistics."""
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                template_count = conn.execute("SELECT COUNT(DISTINCT template_name) FROM versions").fetchone()[0]
                version_count = conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
                migration_count = conn.execute("SELECT COUNT(*) FROM migrations").fetchone()[0]
                pending_count = conn.execute("SELECT COUNT(*) FROM migrations WHERE status = 'pending'").fetchone()[0]
                deployment_count = conn.execute("SELECT COUNT(*) FROM deployed").fetchone()[0]

        return {
            "templates": template_count,
            "total_versions": version_count,
            "total_migrations": migration_count,
            "pending_migrations": pending_count,
            "deployments": deployment_count,
        }

    # -- Private --

    @staticmethod
    def _compute_hash(
        files: Dict[str, str],
        required_vars: List[str],
        optional_vars: List[str],
    ) -> str:
        """Compute a deterministic hash of the template schema."""
        payload = json.dumps({
            "files": {k: files[k] for k in sorted(files.keys())},
            "required_vars": sorted(required_vars),
            "optional_vars": sorted(optional_vars),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _compute_changes(old: TemplateVersion, new: TemplateVersion) -> List[Dict[str, Any]]:
        """Compute the list of changes between two template versions."""
        changes: List[Dict[str, Any]] = []

        old_files = set(old.files.keys())
        new_files = set(new.files.keys())

        for f in new_files - old_files:
            changes.append({"type": "file_added", "file": f})
        for f in old_files - new_files:
            changes.append({"type": "file_removed", "file": f})
        for f in old_files & new_files:
            if old.files[f] != new.files[f]:
                changes.append({"type": "file_modified", "file": f})

        old_req = set(old.required_vars)
        new_req = set(new.required_vars)
        for v in new_req - old_req:
            changes.append({"type": "required_var_added", "var": v})
        for v in old_req - new_req:
            changes.append({"type": "required_var_removed", "var": v})

        old_opt = set(old.optional_vars)
        new_opt = set(new.optional_vars)
        for v in new_opt - old_opt:
            changes.append({"type": "optional_var_added", "var": v})
        for v in old_opt - new_opt:
            changes.append({"type": "optional_var_removed", "var": v})

        return changes

    def _get_migration(self, migration_id: str) -> Optional[SchemaMigration]:
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM migrations WHERE migration_id = ?", (migration_id,)
                ).fetchone()
        if row is None:
            return None
        return self._row_to_migration(row)

    def _update_migration_status(self, migration_id: str, status: str, applied_at: Optional[datetime] = None) -> None:
        with self._lock:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    "UPDATE migrations SET status = ?, applied_at = ? WHERE migration_id = ?",
                    (status, applied_at.isoformat() if applied_at else None, migration_id),
                )

    def _row_to_version(self, row: sqlite3.Row) -> TemplateVersion:
        def _parse_dt(val):
            if not val:
                return datetime.now(timezone.utc)
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return TemplateVersion(
            version_id=row["version_id"],
            template_name=row["template_name"],
            version=row["version"],
            schema_hash=row["schema_hash"],
            files=json.loads(row["files"] or "{}"),
            required_vars=json.loads(row["required_vars"] or "[]"),
            optional_vars=json.loads(row["optional_vars"] or "[]"),
            changelog=row["changelog"] or "",
            created_at=_parse_dt(row["created_at"]),
        )

    def _row_to_migration(self, row: sqlite3.Row) -> SchemaMigration:
        def _parse_dt(val):
            if not val:
                return None
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return SchemaMigration(
            migration_id=row["migration_id"],
            template_name=row["template_name"],
            from_version=row["from_version"],
            to_version=row["to_version"],
            changes=json.loads(row["changes"] or "[]"),
            status=row["status"] or "pending",
            applied_at=_parse_dt(row["applied_at"]),
            created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
        )
