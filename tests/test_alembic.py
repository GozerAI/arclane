"""Tests for Alembic migrations."""

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_single_head():
    """Verify there's exactly one migration head (no branching)."""
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected 1 head, got {len(heads)}: {heads}"


def test_migration_chain_complete():
    """Verify migration chain from base to head is unbroken."""
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    revisions = list(script.walk_revisions())
    assert len(revisions) >= 1, "Expected at least 1 migration"
