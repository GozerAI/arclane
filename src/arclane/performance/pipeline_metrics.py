"""Pipeline metrics collection with OpenTelemetry-compatible interface.

Item 163: Collects metrics for the cycle execution pipeline, including
timing, success rates, and throughput. Uses an interface compatible with
OpenTelemetry conventions but works without the full OTel SDK.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from arclane.core.logging import get_logger

log = get_logger("performance.pipeline_metrics")


@dataclass
class MetricPoint:
    """A single metric data point."""
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Counter:
    """Monotonically increasing counter (OTel-compatible)."""

    def __init__(self, name: str, description: str = "", unit: str = ""):
        self.name = name
        self.description = description
        self.unit = unit
        self._values: dict[str, float] = {}

    def add(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = _labels_key(labels)
        self._values[key] = self._values.get(key, 0) + value

    def get(self, labels: dict[str, str] | None = None) -> float:
        return self._values.get(_labels_key(labels), 0)

    def collect(self) -> list[MetricPoint]:
        return [
            MetricPoint(name=self.name, value=v, labels=_parse_key(k))
            for k, v in self._values.items()
        ]


class Histogram:
    """Records distribution of values (OTel-compatible)."""

    def __init__(
        self, name: str, description: str = "", unit: str = "",
        boundaries: list[float] | None = None,
    ):
        self.name = name
        self.description = description
        self.unit = unit
        self._boundaries = boundaries or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        self._records: dict[str, list[float]] = {}

    def record(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = _labels_key(labels)
        self._records.setdefault(key, []).append(value)

    def get_summary(self, labels: dict[str, str] | None = None) -> dict:
        key = _labels_key(labels)
        values = self._records.get(key, [])
        if not values:
            return {"count": 0, "sum": 0, "min": 0, "max": 0, "avg": 0}
        return {
            "count": len(values),
            "sum": round(sum(values), 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "avg": round(sum(values) / len(values), 4),
        }

    def collect(self) -> list[MetricPoint]:
        points = []
        for key, values in self._records.items():
            labels = _parse_key(key)
            if values:
                points.append(MetricPoint(
                    name=f"{self.name}_sum",
                    value=sum(values),
                    labels=labels,
                ))
                points.append(MetricPoint(
                    name=f"{self.name}_count",
                    value=len(values),
                    labels=labels,
                ))
        return points


class Gauge:
    """Point-in-time value (OTel-compatible)."""

    def __init__(self, name: str, description: str = "", unit: str = ""):
        self.name = name
        self.description = description
        self.unit = unit
        self._values: dict[str, float] = {}

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        self._values[_labels_key(labels)] = value

    def get(self, labels: dict[str, str] | None = None) -> float:
        return self._values.get(_labels_key(labels), 0)

    def collect(self) -> list[MetricPoint]:
        return [
            MetricPoint(name=self.name, value=v, labels=_parse_key(k))
            for k, v in self._values.items()
        ]


def _labels_key(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _parse_key(key: str) -> dict[str, str]:
    if not key:
        return {}
    return dict(pair.split("=", 1) for pair in key.split(",") if "=" in pair)


class PipelineMetrics:
    """Collects metrics for the Arclane cycle execution pipeline."""

    def __init__(self):
        self.cycles_started = Counter(
            "arclane_cycles_started_total",
            "Total number of cycles started",
        )
        self.cycles_completed = Counter(
            "arclane_cycles_completed_total",
            "Total number of cycles completed",
        )
        self.cycles_failed = Counter(
            "arclane_cycles_failed_total",
            "Total number of cycles failed",
        )
        self.cycle_duration = Histogram(
            "arclane_cycle_duration_seconds",
            "Duration of cycle execution",
            unit="s",
            boundaries=[1, 5, 10, 30, 60, 120, 300, 600],
        )
        self.tasks_processed = Counter(
            "arclane_tasks_processed_total",
            "Total tasks processed across all cycles",
        )
        self.active_cycles = Gauge(
            "arclane_active_cycles",
            "Currently running cycles",
        )
        self.csuite_request_duration = Histogram(
            "arclane_csuite_request_duration_seconds",
            "Duration of requests to C-Suite",
            unit="s",
        )
        self.webhook_deliveries = Counter(
            "arclane_webhook_deliveries_total",
            "Total webhook delivery attempts",
        )
        self.container_builds = Counter(
            "arclane_container_builds_total",
            "Total container build operations",
        )
        self.container_build_duration = Histogram(
            "arclane_container_build_duration_seconds",
            "Duration of container builds",
            unit="s",
        )

    def record_cycle_start(self, trigger: str, plan: str) -> None:
        self.cycles_started.add(labels={"trigger": trigger, "plan": plan})
        current = self.active_cycles.get()
        self.active_cycles.set(current + 1)

    def record_cycle_complete(self, trigger: str, plan: str, duration_s: float, tasks: int) -> None:
        self.cycles_completed.add(labels={"trigger": trigger, "plan": plan})
        self.cycle_duration.record(duration_s, labels={"trigger": trigger})
        self.tasks_processed.add(tasks, labels={"trigger": trigger})
        current = self.active_cycles.get()
        self.active_cycles.set(max(0, current - 1))

    def record_cycle_failure(self, trigger: str, plan: str) -> None:
        self.cycles_failed.add(labels={"trigger": trigger, "plan": plan})
        current = self.active_cycles.get()
        self.active_cycles.set(max(0, current - 1))

    def collect_all(self) -> list[MetricPoint]:
        """Collect all metric points for export."""
        points = []
        for attr in dir(self):
            obj = getattr(self, attr)
            if hasattr(obj, "collect"):
                points.extend(obj.collect())
        return points

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text exposition format."""
        lines = []
        for point in self.collect_all():
            label_str = ""
            if point.labels:
                pairs = ",".join(f'{k}="{v}"' for k, v in point.labels.items())
                label_str = f"{{{pairs}}}"
            lines.append(f"{point.name}{label_str} {point.value}")
        return "\n".join(lines) + "\n" if lines else ""


# Singleton
pipeline_metrics = PipelineMetrics()
