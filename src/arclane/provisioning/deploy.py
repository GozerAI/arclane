"""App deployment — Docker-based per-tenant containers.

Each business gets a container built from a template, running behind Caddy.
Port allocation uses the database to avoid collisions across restarts.

Docker SDK is optional — in development without Docker, deployment is
simulated (logged but not executed).
"""

import json
import re
import shutil
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.config import settings
from arclane.core.logging import get_logger
from arclane.performance.container_build import container_memory_monitor
from arclane.performance.parallel_templates import parallel_instantiator
from arclane.provisioning.subdomain import update_subdomain_upstream

log = get_logger("provisioning.deploy")

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"
WORKSPACES_DIR = Path(settings.workspaces_root)
BASE_PORT = 9000
HEALTH_PATHS = ("/health", "/")

# Docker client — lazy init, optional
_docker_client = None
_docker_available = None


def _get_docker():
    """Get Docker client, returning None if Docker is unavailable."""
    global _docker_client, _docker_available

    if _docker_available is False:
        return None
    if _docker_client is not None:
        return _docker_client

    try:
        import docker
        _docker_client = docker.from_env()
        _docker_client.ping()
        _docker_available = True
        log.info("Docker connected")
        return _docker_client
    except Exception:
        _docker_available = False
        log.warning("Docker unavailable — deployments will be simulated")
        return None


def _validate_slug(slug: str) -> None:
    """Validate slug is safe for use as a Docker container/image name."""
    if not re.match(r'^[a-z0-9][a-z0-9-]{0,62}$', slug):
        raise ValueError(f"Invalid slug: {slug!r}")


def _ensure_tenant_network(client):
    """Ensure the isolated tenant network exists, creating it if needed."""
    try:
        import docker as docker_mod
        try:
            return client.networks.get("arclane-tenants")
        except docker_mod.errors.NotFound:
            log.info("Creating isolated tenant network: arclane-tenants")
            return client.networks.create("arclane-tenants", driver="bridge", internal=True)
    except Exception:
        log.warning("Failed to create/get tenant network (non-fatal)")
        return None


async def allocate_port(session: AsyncSession) -> int:
    """Allocate the next available port from the database."""
    from arclane.models.tables import Business

    result = await session.execute(
        select(func.max(Business.container_port))
    )
    max_port = result.scalar()
    return (max_port or BASE_PORT - 1) + 1


async def deploy_template(
    slug: str,
    template: str,
    session: AsyncSession | None = None,
    business_name: str | None = None,
    business_description: str | None = None,
) -> tuple[int, str | None]:
    """Deploy an app from a template for a business.

    Returns a tuple of (allocated port, container_id or None).
    """
    _validate_slug(slug)

    template_dir = TEMPLATES_DIR / template
    if not template_dir.exists():
        raise FileNotFoundError(f"Template not found: {template}")

    # Allocate port
    if session:
        port = await allocate_port(session)
    else:
        port = BASE_PORT  # Fallback for tests

    # Copy template to workspace (parallel for speed)
    workspace = WORKSPACES_DIR / slug
    workspace.mkdir(parents=True, exist_ok=True)
    display_name = business_name or slug.replace("-", " ").title()
    description = business_description or ""
    template_vars = {
        "BUSINESS_SLUG": slug,
        "BUSINESS_NAME": display_name,
        "BUSINESS_DESCRIPTION": description,
        "PORT": str(port),
        # AI-injectable defaults — replaced by content_injector after first cycle
        "HEADLINE": display_name,
        "SUBHEADLINE": description[:200] if description else "Launching soon.",
        "CTA_TEXT": "Get Started",
    }
    try:
        result = await parallel_instantiator.instantiate(
            template_dir, workspace,
            variables=template_vars,
        )
        log.info(
            "Template '%s' instantiated to %s (%d files, %.0fms)",
            template, workspace, result.total_files, result.duration_ms,
        )
    except Exception:
        log.warning("Parallel instantiation failed, falling back to copytree")
        shutil.copytree(template_dir, workspace, dirs_exist_ok=True)
        # Apply variable substitution to text files in the fallback path
        for file_path in workspace.rglob("*"):
            if file_path.is_file() and file_path.suffix in (".html", ".js", ".json", ".yaml", ".yml", ".txt", ".md"):
                try:
                    content = file_path.read_text(encoding="utf-8")
                    for key, value in template_vars.items():
                        content = content.replace(f"{{{{{key}}}}}", value)
                    file_path.write_text(content, encoding="utf-8")
                except Exception:
                    pass
    _write_workspace_manifest(slug, template, workspace, port)

    # Build and run container
    docker = _get_docker()
    container_id = None

    if docker:
        # Ensure isolated network exists
        _ensure_tenant_network(docker)

        try:
            # Build image
            image_tag = f"arclane-{slug}"
            log.info("Building image %s from %s", image_tag, workspace)
            image, build_logs = docker.images.build(
                path=str(workspace),
                tag=image_tag,
                rm=True,
            )

            # Stop existing container if any
            try:
                old = docker.containers.get(f"arclane-{slug}")
                old.stop(timeout=10)
                old.remove()
                log.info("Removed existing container arclane-%s", slug)
            except Exception:
                log.debug("No existing container to remove for arclane-%s (expected on first deploy)", slug)

            # Run new container with resource limits and network isolation
            container_name = f"arclane-{slug}"
            container = docker.containers.run(
                image_tag,
                name=container_name,
                ports={f"{port}/tcp": ("127.0.0.1", port)},
                detach=True,
                restart_policy={"Name": "unless-stopped"},
                mem_limit="256m",
                cpu_quota=50000,  # 50% of one CPU
                pids_limit=100,
                network="arclane-tenants",
                labels={
                    "arclane.slug": slug,
                    "arclane.managed": "true",
                },
            )
            container_id = container.short_id
            log.info(
                "Container deployed: %s (id=%s) on port %d",
                slug, container_id, port,
            )
        except Exception:
            log.exception("Docker deployment failed for %s", slug)
            raise
    else:
        log.info(
            "Simulated deployment: arclane-%s on port %d (Docker unavailable)",
            slug, port,
        )

    # Update Caddy to route subdomain to this container
    try:
        await update_subdomain_upstream(slug, port)
    except Exception:
        log.exception("Caddy route update failed for %s (non-fatal)", slug)

    return port, container_id


async def wait_for_tenant_surface(
    port: int,
    *,
    attempts: int = 10,
    interval_s: float = 0.5,
) -> bool:
    """Probe a tenant surface until it responds or the retry budget is exhausted."""
    if port <= 0:
        return False

    base_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
        for attempt in range(attempts):
            for path in HEALTH_PATHS:
                try:
                    response = await client.get(f"{base_url}{path}")
                    if 200 <= response.status_code < 500:
                        return True
                except Exception:
                    continue
            if attempt < attempts - 1:
                await asyncio.sleep(interval_s)
    return False


def _write_workspace_manifest(slug: str, template: str, workspace: Path, port: int) -> None:
    """Write a workspace manifest so tenant code storage is inspectable."""
    manifest = {
        "slug": slug,
        "template": template,
        "domain": settings.domain,
        "subdomain": f"{slug}.{settings.domain}",
        "workspace_path": str(workspace),
        "manifest_version": 1,
        "storage_mode": "workspace_copy",
        "allocated_port": port,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (workspace / "arclane-workspace.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )


async def stop_container(slug: str) -> bool:
    """Stop and remove a business's container."""
    _validate_slug(slug)

    docker = _get_docker()
    if not docker:
        log.info("Simulated stop: arclane-%s (Docker unavailable)", slug)
        return True

    try:
        container = docker.containers.get(f"arclane-{slug}")
        container.stop(timeout=10)
        container.remove()
        log.info("Container stopped and removed: arclane-%s", slug)
        return True
    except Exception:
        log.exception("Failed to stop container arclane-%s", slug)
        return False


async def check_container_health(slug: str) -> dict:
    """Check if a business's container is running and healthy."""
    docker = _get_docker()
    if not docker:
        return {"status": "unknown", "reason": "docker_unavailable"}

    try:
        container = docker.containers.get(f"arclane-{slug}")
        result = {
            "status": container.status,
            "running": container.status == "running",
            "short_id": container.short_id,
        }
        # Check memory usage if running
        if container.status == "running":
            mem_stats = await container_memory_monitor.check_container_memory(slug)
            if mem_stats:
                result["memory"] = mem_stats
        return result
    except Exception:
        return {"status": "not_found", "running": False}


async def list_managed_containers() -> list[dict]:
    """List all Arclane-managed containers."""
    docker = _get_docker()
    if not docker:
        return []

    containers = docker.containers.list(
        all=True,
        filters={"label": "arclane.managed=true"},
    )
    return [
        {
            "name": c.name,
            "slug": c.labels.get("arclane.slug", "unknown"),
            "status": c.status,
            "short_id": c.short_id,
        }
        for c in containers
    ]
