"""Tests for offline cycle execution (item 772)."""

import pytest

from arclane.offline.cycle_executor import (
    OfflineCycleExecutor,
    OfflineCycleResult,
    OfflineTaskResult,
)


@pytest.fixture
def executor():
    return OfflineCycleExecutor()


class TestOfflineTaskResult:
    def test_to_dict(self):
        r = OfflineTaskResult(area="strategy", action="define_mission", output="test output")
        d = r.to_dict()
        assert d["area"] == "strategy"
        assert d["is_deterministic"] is True


class TestOfflineCycleResult:
    def test_empty_result(self):
        r = OfflineCycleResult(business_name="X", slug="x")
        assert r.task_count == 0
        assert r.completed_count == 0
        assert r.success_rate == 0.0

    def test_with_tasks(self):
        r = OfflineCycleResult(business_name="X", slug="x", tasks=[
            OfflineTaskResult(area="a", action="a1", status="completed"),
            OfflineTaskResult(area="b", action="b1", status="completed"),
            OfflineTaskResult(area="c", action="c1", status="error"),
        ])
        assert r.task_count == 3
        assert r.completed_count == 2
        assert r.success_rate == pytest.approx(2 / 3)

    def test_to_dict(self):
        r = OfflineCycleResult(business_name="Test", slug="test")
        d = r.to_dict()
        assert d["business_name"] == "Test"
        assert d["is_offline"] is True


class TestOfflineCycleExecutor:
    def test_execute_full_cycle(self, executor):
        result = executor.execute("Acme", "acme", description="Widget company")
        assert result.status == "completed"
        assert result.task_count > 0
        assert result.completed_count == result.task_count
        assert result.is_offline
        assert len(result.areas_covered) > 0

    def test_deterministic_warning(self, executor):
        result = executor.execute("Acme", "acme")
        assert "deterministic_mode_no_llm" in result.warnings

    def test_execute_specific_areas(self):
        executor = OfflineCycleExecutor(areas=["strategy", "content"])
        result = executor.execute("Acme", "acme")
        assert set(result.areas_covered) == {"strategy", "content"}

    def test_execute_with_task_description(self, executor):
        result = executor.execute("Acme", "acme", task_description="Create a blog post about widgets")
        assert result.task_count == 1
        assert result.tasks[0].action == "custom_task"
        assert "blog post" in result.tasks[0].output.lower() or "Acme" in result.tasks[0].output

    def test_all_tasks_deterministic(self, executor):
        result = executor.execute("Acme", "acme")
        for task in result.tasks:
            assert task.is_deterministic

    def test_has_local_model_false(self, executor):
        assert not executor.has_local_model

    def test_has_local_model_true(self):
        executor = OfflineCycleExecutor(local_model_fn=lambda s, u: "response")
        assert executor.has_local_model

    def test_local_model_used(self):
        calls = []

        def mock_model(system, user):
            calls.append((system, user))
            return "AI generated content"

        executor = OfflineCycleExecutor(local_model_fn=mock_model, areas=["strategy"])
        result = executor.execute("Acme", "acme")
        assert len(calls) > 0
        assert any(not t.is_deterministic for t in result.tasks)

    def test_local_model_fallback_on_error(self):
        def failing_model(system, user):
            raise RuntimeError("model crashed")

        executor = OfflineCycleExecutor(local_model_fn=failing_model, areas=["strategy"])
        result = executor.execute("Acme", "acme")
        # Should fall back to deterministic
        assert all(t.is_deterministic for t in result.tasks)
        assert result.status == "completed"

    def test_local_model_fallback_on_empty(self):
        executor = OfflineCycleExecutor(local_model_fn=lambda s, u: "", areas=["strategy"])
        result = executor.execute("Acme", "acme")
        assert all(t.is_deterministic for t in result.tasks)

    def test_execute_area(self, executor):
        results = executor.execute_area("market_research", "Acme", "acme")
        assert len(results) == 3  # 3 tasks defined for market_research
        assert all(r.area == "market_research" for r in results)

    def test_execute_area_empty(self, executor):
        results = executor.execute_area("nonexistent", "Acme", "acme")
        assert results == []

    def test_list_areas(self, executor):
        areas = executor.list_areas()
        assert "strategy" in areas
        assert "content" in areas
        assert "finance" in areas

    def test_list_tasks(self, executor):
        tasks = executor.list_tasks("strategy")
        assert len(tasks) == 3
        assert tasks[0]["action"] == "define_mission"

    def test_interpolation_in_output(self, executor):
        result = executor.execute("Widget Co", "widget-co", template="saas-app")
        # At least some tasks should have the business name interpolated
        has_interpolation = any(
            "Widget Co" in t.output or "widget-co" in t.output.lower() or "saas-app" in t.output
            for t in result.tasks if t.output
        )
        assert has_interpolation

    def test_cycle_id_propagated(self, executor):
        result = executor.execute("Acme", "acme", cycle_id=42)
        assert result.cycle_id == 42

    def test_unknown_area_warning(self):
        executor = OfflineCycleExecutor(areas=["strategy", "unknown_area"])
        result = executor.execute("Acme", "acme")
        assert any("unknown_area" in w for w in result.warnings)

    def test_completed_at_set(self, executor):
        result = executor.execute("Acme", "acme")
        assert result.completed_at is not None
