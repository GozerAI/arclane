"""Tests for offline container management (item 782)."""

import pytest

from arclane.offline.container_manager import (
    OfflineContainerManager,
    ContainerState,
    ContainerAction,
)


@pytest.fixture
def mgr(tmp_path):
    return OfflineContainerManager(db_path=tmp_path / "containers.db")


class TestOfflineContainerManager:
    def test_register_container(self, mgr):
        rec = mgr.register_container("acme-corp", template="content-site", port=3001)
        assert rec.business_slug == "acme-corp"
        assert rec.state == ContainerState.CREATED
        assert rec.port == 3001

    def test_get_container(self, mgr):
        mgr.register_container("acme", template="saas-app")
        rec = mgr.get_container("acme")
        assert rec is not None
        assert rec.template == "saas-app"

    def test_get_container_missing(self, mgr):
        assert mgr.get_container("nope") is None

    def test_get_all_containers(self, mgr):
        mgr.register_container("a")
        mgr.register_container("b")
        assert len(mgr.get_all_containers()) == 2

    def test_update_state(self, mgr):
        mgr.register_container("acme")
        assert mgr.update_state("acme", ContainerState.RUNNING)
        rec = mgr.get_container("acme")
        assert rec.state == ContainerState.RUNNING

    def test_update_health(self, mgr):
        mgr.register_container("acme")
        assert mgr.update_health("acme", "healthy")
        rec = mgr.get_container("acme")
        assert rec.health_status == "healthy"
        assert rec.last_health_check is not None

    def test_remove_container(self, mgr):
        mgr.register_container("acme")
        assert mgr.remove_container("acme")
        assert mgr.get_container("acme") is None

    def test_remove_nonexistent(self, mgr):
        assert not mgr.remove_container("nope")

    def test_queue_action(self, mgr):
        mgr.register_container("acme")
        action = mgr.queue_action("acme", ContainerAction.START)
        assert action.status == "pending"
        assert action.action == ContainerAction.START

    def test_queue_action_sets_pending_state(self, mgr):
        mgr.register_container("acme")
        mgr.queue_action("acme", ContainerAction.START)
        rec = mgr.get_container("acme")
        assert rec.state == ContainerState.PENDING_START

    def test_get_pending_actions(self, mgr):
        mgr.register_container("acme")
        mgr.queue_action("acme", ContainerAction.START)
        mgr.queue_action("acme", ContainerAction.STOP)
        pending = mgr.get_pending_actions()
        assert len(pending) == 2

    def test_get_pending_actions_by_slug(self, mgr):
        mgr.register_container("a")
        mgr.register_container("b")
        mgr.queue_action("a", ContainerAction.START)
        mgr.queue_action("b", ContainerAction.START)
        assert len(mgr.get_pending_actions("a")) == 1

    def test_complete_action(self, mgr):
        mgr.register_container("acme")
        action = mgr.queue_action("acme", ContainerAction.CREATE)
        assert mgr.complete_action(action.id)
        assert len(mgr.get_pending_actions()) == 0

    def test_fail_action(self, mgr):
        mgr.register_container("acme")
        action = mgr.queue_action("acme", ContainerAction.START)
        assert mgr.fail_action(action.id, "docker not found")

    def test_replay_actions_success(self, mgr):
        mgr.register_container("acme")
        mgr.queue_action("acme", ContainerAction.START)
        mgr.queue_action("acme", ContainerAction.HEALTH_CHECK)

        result = mgr.replay_actions(lambda slug, action, params: True)
        assert result["completed"] == 2
        assert result["failed"] == 0

    def test_replay_actions_failure(self, mgr):
        mgr.register_container("acme")
        mgr.queue_action("acme", ContainerAction.START)

        def handler(slug, action, params):
            raise RuntimeError("docker daemon offline")

        result = mgr.replay_actions(handler)
        assert result["failed"] == 1

    def test_clear_completed(self, mgr):
        mgr.register_container("acme")
        a = mgr.queue_action("acme", ContainerAction.START)
        mgr.complete_action(a.id)
        removed = mgr.clear_completed()
        assert removed == 1

    def test_check_health_all_running(self, mgr):
        mgr.register_container("acme")
        mgr.update_state("acme", ContainerState.RUNNING)
        mgr.update_health("acme", "healthy")
        results = mgr.check_health_all()
        assert len(results) == 1

    def test_check_health_stopped(self, mgr):
        mgr.register_container("acme")
        mgr.update_state("acme", ContainerState.STOPPED)
        results = mgr.check_health_all()
        assert results[0]["health"] == "stopped"

    def test_check_health_failed(self, mgr):
        mgr.register_container("acme")
        mgr.update_state("acme", ContainerState.FAILED)
        results = mgr.check_health_all()
        assert results[0]["health"] == "failed"

    def test_check_health_pending(self, mgr):
        mgr.register_container("acme")
        mgr.update_state("acme", ContainerState.PENDING_CREATE)
        results = mgr.check_health_all()
        assert results[0]["health"] == "pending"

    def test_stats(self, mgr):
        mgr.register_container("a")
        mgr.register_container("b")
        mgr.queue_action("a", ContainerAction.START)
        stats = mgr.stats()
        assert stats["total_containers"] == 2
        assert stats["pending_actions"] == 1

    def test_register_with_config(self, mgr):
        rec = mgr.register_container("acme", config={"env": {"NODE_ENV": "production"}})
        assert rec.config["env"]["NODE_ENV"] == "production"

    def test_register_replaces(self, mgr):
        mgr.register_container("acme", template="old")
        mgr.register_container("acme", template="new")
        rec = mgr.get_container("acme")
        assert rec.template == "new"
        assert len(mgr.get_all_containers()) == 1

    def test_custom_container_id(self, mgr):
        rec = mgr.register_container("acme", container_id="abc123")
        assert rec.container_id == "abc123"
