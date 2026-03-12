"""Tests for container deployment and lifecycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_allocate_port_first(db):
    """First port allocation returns BASE_PORT."""
    from arclane.provisioning.deploy import allocate_port, BASE_PORT

    async with db() as session:
        port = await allocate_port(session)
    assert port == BASE_PORT


async def test_allocate_port_sequential(db):
    """Ports are allocated sequentially from existing max."""
    from arclane.provisioning.deploy import allocate_port

    async with db() as session:
        biz = Business(
            slug="port-test",
            name="Port Test",
            description="Test",
            owner_email="test@test.com",
            container_port=9005,
        )
        session.add(biz)
        await session.commit()
        port = await allocate_port(session)
    assert port == 9006


async def test_deploy_template_not_found():
    """Deploying a nonexistent template raises FileNotFoundError."""
    from arclane.provisioning.deploy import deploy_template

    with pytest.raises(FileNotFoundError):
        await deploy_template("test", "nonexistent-template")


async def test_deploy_simulated_without_docker(db, tmp_path):
    """Deployment works in simulated mode when Docker is unavailable."""
    from arclane.provisioning import deploy

    # Force Docker unavailable
    deploy._docker_available = False
    deploy._docker_client = None

    template_dir = tmp_path / "test-template"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
         patch.object(deploy, "WORKSPACES_DIR", tmp_path / "workspaces"), \
         patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
        async with db() as session:
            port, container_id = await deploy.deploy_template("sim-test", "test-template", session)

    assert port >= 9000
    assert container_id is None  # No real container


async def test_stop_container_without_docker():
    """Stop returns True in simulated mode."""
    from arclane.provisioning import deploy
    deploy._docker_available = False
    deploy._docker_client = None

    result = await deploy.stop_container("test-slug")
    assert result is True


async def test_check_health_without_docker():
    """Health check returns unknown when Docker unavailable."""
    from arclane.provisioning import deploy
    deploy._docker_available = False
    deploy._docker_client = None

    health = await deploy.check_container_health("test-slug")
    assert health["status"] == "unknown"


async def test_list_managed_without_docker():
    """List returns empty when Docker unavailable."""
    from arclane.provisioning import deploy
    deploy._docker_available = False
    deploy._docker_client = None

    containers = await deploy.list_managed_containers()
    assert containers == []


async def test_deploy_with_docker(db, tmp_path):
    """Deployment uses Docker SDK when available."""
    from arclane.provisioning import deploy

    # Mock Docker client
    mock_docker = MagicMock()
    mock_image = MagicMock()
    mock_container = MagicMock()
    mock_container.short_id = "abc123"
    mock_docker.images.build.return_value = (mock_image, [])
    mock_docker.containers.run.return_value = mock_container
    mock_docker.containers.get.side_effect = Exception("not found")  # No existing container

    deploy._docker_available = True
    deploy._docker_client = mock_docker

    template_dir = tmp_path / "docker-template"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    try:
        with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
             patch.object(deploy, "WORKSPACES_DIR", tmp_path / "workspaces"), \
             patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
            async with db() as session:
                port, container_id = await deploy.deploy_template("docker-test", "docker-template", session)

        assert port >= 9000
        assert container_id == "abc123"
        mock_docker.images.build.assert_called_once()
        mock_docker.containers.run.assert_called_once()
    finally:
        deploy._docker_available = None
        deploy._docker_client = None
