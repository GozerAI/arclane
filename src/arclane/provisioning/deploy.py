"""App deployment — Docker-based per-tenant containers.

Each business gets a container built from a template, running behind Caddy.
Port allocation uses the database to avoid collisions across restarts.

Docker SDK is optional — in development without Docker, deployment is
simulated (logged but not executed).
"""

import shutil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.provisioning.subdomain import update_subdomain_upstream

log = get_logger("provisioning.deploy")

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"
WORKSPACES_DIR = Path("/var/arclane/workspaces")
BASE_PORT = 9000

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


async def allocate_port(session: AsyncSession) -> int:
    """Allocate the next available port from the database."""
    from arclane.models.tables import Business

    result = await session.execute(
        select(func.max(Business.container_port))
    )
    max_port = result.scalar()
    return (max_port or BASE_PORT - 1) + 1


async def deploy_template(
    slug: str, template: str, session: AsyncSession | None = None
) -> int:
    """Deploy an app from a template for a business.

    Returns the allocated port number.
    """
    template_dir = TEMPLATES_DIR / template
    if not template_dir.exists():
        raise FileNotFoundError(f"Template not found: {template}")

    # Allocate port
    if session:
        port = await allocate_port(session)
    else:
        port = BASE_PORT  # Fallback for tests

    # Copy template to workspace
    workspace = WORKSPACES_DIR / slug
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, workspace, dirs_exist_ok=True)
    log.info("Template '%s' copied to %s", template, workspace)

    # Build and run container
    docker = _get_docker()
    container_id = None

    if docker:
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
                pass

            # Run new container
            container = docker.containers.run(
                image_tag,
                name=f"arclane-{slug}",
                ports={f"{port}/tcp": port},
                detach=True,
                restart_policy={"Name": "unless-stopped"},
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


async def stop_container(slug: str) -> bool:
    """Stop and remove a business's container."""
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
        return {
            "status": container.status,
            "running": container.status == "running",
            "short_id": container.short_id,
        }
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
