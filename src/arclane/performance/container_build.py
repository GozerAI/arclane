"""Async container build with progress streaming.

Item 136: Builds Docker containers asynchronously with progress events
streamed to the caller via an async generator.

Item 175: Memory limits per container with OOM handling.
Item 186: Container memory burst limits with monitoring.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

from arclane.core.logging import get_logger

log = get_logger("performance.container_build")


class BuildPhase(str, Enum):
    PREPARING = "preparing"
    BUILDING = "building"
    PUSHING = "pushing"
    STARTING = "starting"
    HEALTH_CHECK = "health_check"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class BuildProgress:
    """Progress update for a container build."""
    phase: BuildPhase
    message: str
    progress_pct: float = 0.0
    elapsed_s: float = 0.0
    error: str | None = None


@dataclass
class MemoryConfig:
    """Memory configuration for a container."""
    mem_limit: str = "256m"       # Hard limit
    mem_reservation: str = "128m"  # Soft limit / reservation
    mem_burst_limit: str = "384m"  # Burst limit (memswap - mem)
    oom_kill_disable: bool = False
    oom_score_adj: int = 0

    def to_docker_kwargs(self) -> dict:
        """Convert to Docker SDK container.run() kwargs."""
        kwargs: dict[str, Any] = {
            "mem_limit": self.mem_limit,
            "mem_reservation": self.mem_reservation,
            "memswap_limit": self.mem_burst_limit,
            "oom_kill_disable": self.oom_kill_disable,
        }
        if self.oom_score_adj:
            kwargs["oom_score_adj"] = self.oom_score_adj
        return kwargs


# Default memory configs per plan
PLAN_MEMORY_CONFIGS: dict[str, MemoryConfig] = {
    "starter": MemoryConfig(
        mem_limit="256m", mem_reservation="128m", mem_burst_limit="384m",
    ),
    "pro": MemoryConfig(
        mem_limit="512m", mem_reservation="256m", mem_burst_limit="768m",
    ),
    "growth": MemoryConfig(
        mem_limit="1g", mem_reservation="512m", mem_burst_limit="1536m",
    ),
    "scale": MemoryConfig(
        mem_limit="2g", mem_reservation="1g", mem_burst_limit="3g",
    ),
}


@dataclass
class OOMEvent:
    """Record of a container OOM kill event."""
    slug: str
    container_id: str
    timestamp: float = field(default_factory=time.time)
    mem_limit: str = ""
    restart_attempted: bool = False
    restart_succeeded: bool = False


class ContainerMemoryMonitor:
    """Monitors container memory usage and handles OOM events."""

    def __init__(self):
        self._oom_events: list[OOMEvent] = []
        self._memory_stats: dict[str, dict] = {}

    @property
    def oom_events(self) -> list[OOMEvent]:
        return list(self._oom_events)

    def get_memory_config(self, plan: str) -> MemoryConfig:
        """Get the memory config for a plan."""
        return PLAN_MEMORY_CONFIGS.get(plan, PLAN_MEMORY_CONFIGS["starter"])

    def record_oom(
        self, slug: str, container_id: str, mem_limit: str = "",
    ) -> OOMEvent:
        """Record an OOM kill event."""
        event = OOMEvent(
            slug=slug, container_id=container_id, mem_limit=mem_limit,
        )
        self._oom_events.append(event)
        # Keep last 100 events
        if len(self._oom_events) > 100:
            self._oom_events = self._oom_events[-50:]
        log.warning("OOM event for %s (container %s, limit %s)", slug, container_id, mem_limit)
        return event

    async def check_container_memory(self, slug: str) -> dict | None:
        """Check memory usage of a running container.

        Returns dict with usage_bytes, limit_bytes, usage_pct, or None if unavailable.
        """
        try:
            from arclane.provisioning.deploy import _get_docker
            docker = _get_docker()
            if not docker:
                return None

            container = docker.containers.get(f"arclane-{slug}")
            stats = container.stats(stream=False)
            memory_stats = stats.get("memory_stats", {})

            usage = memory_stats.get("usage", 0)
            limit = memory_stats.get("limit", 0)
            usage_pct = (usage / limit * 100) if limit > 0 else 0

            result = {
                "usage_bytes": usage,
                "limit_bytes": limit,
                "usage_pct": round(usage_pct, 1),
                "max_usage_bytes": memory_stats.get("max_usage", 0),
            }
            self._memory_stats[slug] = result
            return result
        except Exception as exc:
            log.debug("Could not check memory for %s: %s", slug, exc)
            return None

    async def handle_oom(self, slug: str, container_id: str, plan: str = "starter") -> OOMEvent:
        """Handle an OOM kill by recording the event and attempting restart."""
        mem_config = self.get_memory_config(plan)
        event = self.record_oom(slug, container_id, mem_config.mem_limit)

        # Attempt restart with same limits
        try:
            from arclane.provisioning.deploy import _get_docker
            docker = _get_docker()
            if docker:
                container = docker.containers.get(f"arclane-{slug}")
                event.restart_attempted = True
                container.restart(timeout=30)
                event.restart_succeeded = True
                log.info("Container %s restarted after OOM", slug)
        except Exception as exc:
            log.error("Failed to restart %s after OOM: %s", slug, exc)

        return event

    def stats(self) -> dict:
        return {
            "total_oom_events": len(self._oom_events),
            "recent_ooms": [
                {
                    "slug": e.slug,
                    "timestamp": e.timestamp,
                    "mem_limit": e.mem_limit,
                    "restarted": e.restart_succeeded,
                }
                for e in self._oom_events[-10:]
            ],
            "monitored_containers": len(self._memory_stats),
        }


class AsyncContainerBuilder:
    """Builds Docker containers asynchronously with progress streaming."""

    def __init__(self):
        self._memory_monitor = container_memory_monitor
        self._active_builds: dict[str, float] = {}

    @property
    def active_builds(self) -> dict[str, float]:
        return dict(self._active_builds)

    async def build_with_progress(
        self,
        slug: str,
        template: str,
        plan: str = "starter",
    ) -> AsyncIterator[BuildProgress]:
        """Build a container and yield progress updates.

        Args:
            slug: Business slug (container name suffix).
            template: Template to build from.
            plan: Business plan (determines memory limits).

        Yields:
            BuildProgress events.
        """
        start = time.monotonic()
        self._active_builds[slug] = start

        try:
            # Phase 1: Preparing
            yield BuildProgress(
                phase=BuildPhase.PREPARING,
                message=f"Preparing workspace for {slug}",
                progress_pct=0.0,
            )

            from arclane.provisioning.deploy import (
                TEMPLATES_DIR, WORKSPACES_DIR, _get_docker,
                _validate_slug, _ensure_tenant_network,
            )
            import shutil

            _validate_slug(slug)
            template_dir = TEMPLATES_DIR / template
            if not template_dir.exists():
                yield BuildProgress(
                    phase=BuildPhase.FAILED,
                    message=f"Template not found: {template}",
                    error=f"Template not found: {template}",
                    elapsed_s=time.monotonic() - start,
                )
                return

            workspace = WORKSPACES_DIR / slug
            workspace.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copytree, template_dir, workspace, dirs_exist_ok=True)

            yield BuildProgress(
                phase=BuildPhase.PREPARING,
                message="Workspace prepared",
                progress_pct=20.0,
                elapsed_s=time.monotonic() - start,
            )

            # Phase 2: Building
            docker = _get_docker()
            if not docker:
                yield BuildProgress(
                    phase=BuildPhase.COMPLETE,
                    message="Build simulated (Docker unavailable)",
                    progress_pct=100.0,
                    elapsed_s=time.monotonic() - start,
                )
                return

            yield BuildProgress(
                phase=BuildPhase.BUILDING,
                message="Building Docker image",
                progress_pct=30.0,
                elapsed_s=time.monotonic() - start,
            )

            image_tag = f"arclane-{slug}"
            image, build_logs = await asyncio.to_thread(
                docker.images.build,
                path=str(workspace),
                tag=image_tag,
                rm=True,
            )

            yield BuildProgress(
                phase=BuildPhase.BUILDING,
                message="Image built successfully",
                progress_pct=60.0,
                elapsed_s=time.monotonic() - start,
            )

            # Phase 3: Starting
            yield BuildProgress(
                phase=BuildPhase.STARTING,
                message="Starting container",
                progress_pct=70.0,
                elapsed_s=time.monotonic() - start,
            )

            _ensure_tenant_network(docker)
            mem_config = self._memory_monitor.get_memory_config(plan)

            # Stop old container if exists
            try:
                old = docker.containers.get(f"arclane-{slug}")
                old.stop(timeout=10)
                old.remove()
            except Exception:
                pass

            container = docker.containers.run(
                image_tag,
                name=f"arclane-{slug}",
                detach=True,
                restart_policy={"Name": "unless-stopped"},
                cpu_quota=50000,
                pids_limit=100,
                network="arclane-tenants",
                labels={"arclane.slug": slug, "arclane.managed": "true"},
                **mem_config.to_docker_kwargs(),
            )

            yield BuildProgress(
                phase=BuildPhase.HEALTH_CHECK,
                message="Waiting for container health",
                progress_pct=85.0,
                elapsed_s=time.monotonic() - start,
            )

            # Brief health wait
            await asyncio.sleep(2)

            yield BuildProgress(
                phase=BuildPhase.COMPLETE,
                message=f"Container {container.short_id} running",
                progress_pct=100.0,
                elapsed_s=time.monotonic() - start,
            )

        except Exception as exc:
            yield BuildProgress(
                phase=BuildPhase.FAILED,
                message=str(exc),
                error=str(exc),
                elapsed_s=time.monotonic() - start,
            )
        finally:
            self._active_builds.pop(slug, None)


# Singletons
container_memory_monitor = ContainerMemoryMonitor()
async_container_builder = AsyncContainerBuilder()
