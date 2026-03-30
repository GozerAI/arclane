"""Tests for template version management (item 934)."""

import pytest

from arclane.offline.template_versioning import (
    TemplateVersionManager,
    TemplateVersion,
    SchemaMigration,
)


@pytest.fixture
def manager(tmp_path):
    return TemplateVersionManager(db_path=tmp_path / "versions.db")


FILES_V1 = {"index.html": "<h1>{{name}}</h1>", "config.json": '{"v": 1}'}
FILES_V2 = {"index.html": "<h1>{{name}}</h1><p>Updated</p>", "config.json": '{"v": 2}', "about.html": "<h2>About</h2>"}


class TestTemplateVersion:
    def test_to_dict(self):
        v = TemplateVersion(template_name="test", version=1, schema_hash="abc")
        d = v.to_dict()
        assert d["template_name"] == "test"
        assert d["version"] == 1


class TestSchemaMigration:
    def test_change_count(self):
        m = SchemaMigration(changes=[{"type": "file_added", "file": "a.html"}])
        assert m.change_count == 1

    def test_to_dict(self):
        m = SchemaMigration(template_name="test", from_version=1, to_version=2)
        d = m.to_dict()
        assert d["from_version"] == 1
        assert d["to_version"] == 2


class TestTemplateVersionManager:
    def test_register_first_version(self, manager):
        v, m = manager.register_version("site", FILES_V1)
        assert v.version == 1
        assert m is None  # no migration for first version

    def test_register_same_schema_no_bump(self, manager):
        manager.register_version("site", FILES_V1)
        v2, m2 = manager.register_version("site", FILES_V1)
        assert v2.version == 1  # no bump
        assert m2 is None

    def test_register_changed_schema_bumps(self, manager):
        manager.register_version("site", FILES_V1)
        v2, m = manager.register_version("site", FILES_V2)
        assert v2.version == 2
        assert m is not None
        assert m.from_version == 1
        assert m.to_version == 2

    def test_migration_detects_file_added(self, manager):
        manager.register_version("site", FILES_V1)
        _, m = manager.register_version("site", FILES_V2)
        changes = m.changes
        added = [c for c in changes if c["type"] == "file_added"]
        assert any(c["file"] == "about.html" for c in added)

    def test_migration_detects_file_modified(self, manager):
        manager.register_version("site", FILES_V1)
        _, m = manager.register_version("site", FILES_V2)
        modified = [c for c in m.changes if c["type"] == "file_modified"]
        assert len(modified) >= 1

    def test_migration_detects_file_removed(self, manager):
        manager.register_version("site", {"a.html": "a", "b.html": "b"})
        _, m = manager.register_version("site", {"a.html": "a"})
        removed = [c for c in m.changes if c["type"] == "file_removed"]
        assert any(c["file"] == "b.html" for c in removed)

    def test_migration_detects_var_changes(self, manager):
        manager.register_version("site", FILES_V1, required_vars=["name"])
        _, m = manager.register_version("site", FILES_V1, required_vars=["name", "email"])
        added = [c for c in m.changes if c["type"] == "required_var_added"]
        assert any(c["var"] == "email" for c in added)

    def test_get_latest_version(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_version("site", FILES_V2)
        latest = manager.get_latest_version("site")
        assert latest.version == 2

    def test_get_latest_version_missing(self, manager):
        assert manager.get_latest_version("nope") is None

    def test_get_specific_version(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_version("site", FILES_V2)
        v1 = manager.get_version("site", 1)
        assert v1 is not None
        assert v1.version == 1

    def test_list_versions(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_version("site", FILES_V2)
        versions = manager.list_versions("site")
        assert len(versions) == 2
        assert versions[0].version == 1
        assert versions[1].version == 2

    def test_get_migrations(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_version("site", FILES_V2)
        migrations = manager.get_migrations("site")
        assert len(migrations) == 1

    def test_get_pending_migrations(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_version("site", FILES_V2)
        pending = manager.get_pending_migrations("site")
        assert len(pending) == 1
        assert pending[0].status == "pending"

    def test_register_deployment(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_deployment("acme", "site", 1)
        dep = manager.get_deployment_version("acme")
        assert dep is not None
        assert dep["current_version"] == 1

    def test_get_deployment_version_missing(self, manager):
        assert manager.get_deployment_version("nope") is None

    def test_get_outdated_deployments(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_deployment("acme", "site", 1)
        manager.register_version("site", FILES_V2)
        outdated = manager.get_outdated_deployments("site")
        assert len(outdated) == 1
        assert outdated[0]["business_slug"] == "acme"
        assert outdated[0]["versions_behind"] == 1

    def test_apply_migration(self, manager):
        manager.register_version("site", FILES_V1)
        _, m = manager.register_version("site", FILES_V2)
        manager.register_deployment("acme", "site", 1)
        assert manager.apply_migration(m.migration_id, "acme")
        dep = manager.get_deployment_version("acme")
        assert dep["current_version"] == 2

    def test_apply_migration_with_fn(self, manager):
        manager.register_version("site", FILES_V1)
        _, m = manager.register_version("site", FILES_V2)
        manager.register_deployment("acme", "site", 1)
        applied = []
        assert manager.apply_migration(m.migration_id, "acme", apply_fn=lambda mig, slug: (applied.append(slug), True)[1])
        assert "acme" in applied

    def test_apply_migration_fn_failure(self, manager):
        manager.register_version("site", FILES_V1)
        _, m = manager.register_version("site", FILES_V2)
        manager.register_deployment("acme", "site", 1)
        assert not manager.apply_migration(m.migration_id, "acme", apply_fn=lambda mig, slug: False)

    def test_needs_migration(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_deployment("acme", "site", 1)
        assert not manager.needs_migration("acme")
        manager.register_version("site", FILES_V2)
        assert manager.needs_migration("acme")

    def test_needs_migration_no_deployment(self, manager):
        assert not manager.needs_migration("nope")

    def test_migration_path(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_version("site", FILES_V2)
        files_v3 = {"new.html": "<p>v3</p>"}
        manager.register_version("site", files_v3)
        path = manager.migration_path("site", 1)
        assert len(path) == 2
        assert path[0].from_version == 1
        assert path[1].from_version == 2

    def test_stats(self, manager):
        manager.register_version("site", FILES_V1)
        manager.register_version("site", FILES_V2)
        manager.register_deployment("acme", "site", 1)
        s = manager.stats()
        assert s["templates"] == 1
        assert s["total_versions"] == 2
        assert s["total_migrations"] == 1
        assert s["deployments"] == 1

    def test_multiple_templates(self, manager):
        manager.register_version("site-a", FILES_V1)
        manager.register_version("site-b", FILES_V2)
        assert manager.get_latest_version("site-a").version == 1
        assert manager.get_latest_version("site-b").version == 1

    def test_changelog(self, manager):
        manager.register_version("site", FILES_V1)
        v, _ = manager.register_version("site", FILES_V2, changelog="Added about page")
        assert v.changelog == "Added about page"
