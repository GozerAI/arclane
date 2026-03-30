"""Container lifecycle — build, start, stop, health, ports, limits."""

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


@pytest.fixture
def mock_docker():
    """Provide a mocked Docker client for tests."""
    client = MagicMock()
    image = MagicMock()
    container = MagicMock()
    container.short_id = "abc12345"
    container.status = "running"
    container.name = "arclane-test-biz"
    container.labels = {"arclane.slug": "test-biz", "arclane.managed": "true"}
    client.images.build.return_value = (image, [])
    client.containers.run.return_value = container
    client.containers.get.return_value = container
    client.containers.list.return_value = [container]
    return client


async def _create_business(factory, slug, port=None, container_id=None):
    async with factory() as session:
        biz = Business(
            slug=slug,
            name=slug.title(),
            description="Test",
            owner_email="test@test.com",
            container_port=port,
            container_id=container_id,
        )
        session.add(biz)
        await session.commit()
        return biz.id


# --- Port allocation ---


async def test_first_port_is_base_port(db):
    """First port allocation returns BASE_PORT (9000)."""
    from arclane.provisioning.deploy import allocate_port, BASE_PORT

    async with db() as session:
        port = await allocate_port(session)
    assert port == BASE_PORT


async def test_sequential_port_allocation(db):
    """Ports are allocated sequentially after existing max."""
    from arclane.provisioning.deploy import allocate_port

    await _create_business(db, "port1", port=9000)
    await _create_business(db, "port2", port=9001)

    async with db() as session:
        port = await allocate_port(session)
    assert port == 9002


async def test_port_allocation_handles_gaps(db):
    """Port allocation uses max port, not count."""
    from arclane.provisioning.deploy import allocate_port

    # Business at port 9010 with no 9001-9009
    await _create_business(db, "gapped", port=9010)

    async with db() as session:
        port = await allocate_port(session)
    assert port == 9011


async def test_port_allocation_with_null_ports(db):
    """Businesses without ports don't affect allocation."""
    from arclane.provisioning.deploy import allocate_port, BASE_PORT

    await _create_business(db, "no-port", port=None)

    async with db() as session:
        port = await allocate_port(session)
    assert port == BASE_PORT


# --- Container build from template ---


async def test_deploy_builds_docker_image(db, mock_docker, tmp_path):
    """Deploy builds a Docker image from template directory."""
    from arclane.provisioning import deploy

    template_dir = tmp_path / "test-tmpl"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    deploy._docker_available = True
    deploy._docker_client = mock_docker
    mock_docker.containers.get.side_effect = Exception("not found")

    try:
        with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
             patch.object(deploy, "WORKSPACES_DIR", tmp_path / "ws"), \
             patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
            async with db() as session:
                port, cid = await deploy.deploy_template("build-test", "test-tmpl", session)

        mock_docker.images.build.assert_called_once()
        assert cid == "abc12345"
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_deploy_sets_resource_limits(db, mock_docker, tmp_path):
    """Container is created with memory and CPU limits."""
    from arclane.provisioning import deploy

    template_dir = tmp_path / "limited"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    deploy._docker_available = True
    deploy._docker_client = mock_docker
    mock_docker.containers.get.side_effect = Exception("not found")

    try:
        with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
             patch.object(deploy, "WORKSPACES_DIR", tmp_path / "ws"), \
             patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
            async with db() as session:
                await deploy.deploy_template("limited-biz", "limited", session)

        call_kwargs = mock_docker.containers.run.call_args
        assert call_kwargs.kwargs["mem_limit"] == "256m"
        assert call_kwargs.kwargs["cpu_quota"] == 50000
        assert call_kwargs.kwargs["pids_limit"] == 100
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_deploy_uses_tenant_network(db, mock_docker, tmp_path):
    """Container is deployed on the isolated arclane-tenants network."""
    from arclane.provisioning import deploy

    template_dir = tmp_path / "nettest"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    deploy._docker_available = True
    deploy._docker_client = mock_docker
    mock_docker.containers.get.side_effect = Exception("not found")

    try:
        with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
             patch.object(deploy, "WORKSPACES_DIR", tmp_path / "ws"), \
             patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
            async with db() as session:
                await deploy.deploy_template("net-biz", "nettest", session)

        call_kwargs = mock_docker.containers.run.call_args
        assert call_kwargs.kwargs["network"] == "arclane-tenants"
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_deploy_labels_container(db, mock_docker, tmp_path):
    """Container gets arclane labels for management."""
    from arclane.provisioning import deploy

    template_dir = tmp_path / "labeled"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    deploy._docker_available = True
    deploy._docker_client = mock_docker
    mock_docker.containers.get.side_effect = Exception("not found")

    try:
        with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
             patch.object(deploy, "WORKSPACES_DIR", tmp_path / "ws"), \
             patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
            async with db() as session:
                await deploy.deploy_template("labeled-biz", "labeled", session)

        call_kwargs = mock_docker.containers.run.call_args
        labels = call_kwargs.kwargs["labels"]
        assert labels["arclane.slug"] == "labeled-biz"
        assert labels["arclane.managed"] == "true"
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


# --- Container stop and cleanup ---


async def test_stop_container_with_docker(mock_docker):
    """Stop removes the container via Docker SDK."""
    from arclane.provisioning import deploy

    deploy._docker_available = True
    deploy._docker_client = mock_docker

    try:
        result = await deploy.stop_container("test-biz")
        assert result is True
        mock_docker.containers.get.assert_called_with("arclane-test-biz")
        mock_docker.containers.get.return_value.stop.assert_called_once_with(timeout=10)
        mock_docker.containers.get.return_value.remove.assert_called_once()
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_stop_container_without_docker():
    """Stop returns True in simulated mode."""
    from arclane.provisioning import deploy

    deploy._docker_available = False
    deploy._docker_client = None

    result = await deploy.stop_container("some-slug")
    assert result is True


async def test_stop_nonexistent_container(mock_docker):
    """Stop returns False when container doesn't exist."""
    from arclane.provisioning import deploy

    deploy._docker_available = True
    deploy._docker_client = mock_docker
    mock_docker.containers.get.side_effect = Exception("Not found")

    try:
        result = await deploy.stop_container("missing")
        assert result is False
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


# --- Container health monitoring ---


async def test_health_check_running_container(mock_docker):
    """Health check returns running status for active container."""
    from arclane.provisioning import deploy

    deploy._docker_available = True
    deploy._docker_client = mock_docker

    try:
        health = await deploy.check_container_health("test-biz")
        assert health["running"] is True
        assert health["status"] == "running"
        assert health["short_id"] == "abc12345"
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_health_check_stopped_container(mock_docker):
    """Health check returns not-running for stopped container."""
    from arclane.provisioning import deploy

    container = mock_docker.containers.get.return_value
    container.status = "exited"

    deploy._docker_available = True
    deploy._docker_client = mock_docker

    try:
        health = await deploy.check_container_health("stopped-biz")
        assert health["running"] is False
        assert health["status"] == "exited"
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_health_check_missing_container(mock_docker):
    """Health check returns not_found for missing container."""
    from arclane.provisioning import deploy

    mock_docker.containers.get.side_effect = Exception("Not found")

    deploy._docker_available = True
    deploy._docker_client = mock_docker

    try:
        health = await deploy.check_container_health("missing-biz")
        assert health["status"] == "not_found"
        assert health["running"] is False
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_health_check_no_docker():
    """Health check returns unknown when Docker unavailable."""
    from arclane.provisioning import deploy

    deploy._docker_available = False
    deploy._docker_client = None

    health = await deploy.check_container_health("any-slug")
    assert health["status"] == "unknown"
    assert health["reason"] == "docker_unavailable"


# --- List managed containers ---


async def test_list_managed_containers(mock_docker):
    """List returns all arclane-managed containers."""
    from arclane.provisioning import deploy

    deploy._docker_available = True
    deploy._docker_client = mock_docker

    try:
        containers = await deploy.list_managed_containers()
        assert len(containers) == 1
        assert containers[0]["slug"] == "test-biz"
        assert containers[0]["status"] == "running"
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


async def test_list_managed_empty_without_docker():
    """List returns empty when Docker unavailable."""
    from arclane.provisioning import deploy

    deploy._docker_available = False
    deploy._docker_client = None

    containers = await deploy.list_managed_containers()
    assert containers == []


# --- Template validation ---


async def test_deploy_nonexistent_template_raises():
    """Deploying a nonexistent template raises FileNotFoundError."""
    from arclane.provisioning.deploy import deploy_template

    with pytest.raises(FileNotFoundError, match="Template not found"):
        await deploy_template("test-slug", "nonexistent-template")


async def test_deploy_invalid_slug_raises():
    """Invalid slug raises ValueError."""
    from arclane.provisioning.deploy import deploy_template

    with pytest.raises(ValueError, match="Invalid slug"):
        await deploy_template("INVALID_SLUG!", "content-site")


async def test_deploy_empty_slug_raises():
    """Empty slug raises ValueError."""
    from arclane.provisioning.deploy import deploy_template

    with pytest.raises(ValueError, match="Invalid slug"):
        await deploy_template("", "content-site")


# --- Slug validation ---


def test_validate_slug_valid():
    """Valid slugs pass validation."""
    from arclane.provisioning.deploy import _validate_slug

    _validate_slug("my-business")
    _validate_slug("a")
    _validate_slug("test123")
    _validate_slug("a-b-c-d")


def test_validate_slug_invalid_uppercase():
    """Uppercase slug is rejected."""
    from arclane.provisioning.deploy import _validate_slug

    with pytest.raises(ValueError):
        _validate_slug("MyBusiness")


def test_validate_slug_invalid_special_chars():
    """Special characters in slug are rejected."""
    from arclane.provisioning.deploy import _validate_slug

    with pytest.raises(ValueError):
        _validate_slug("my_business!")


def test_validate_slug_starting_with_dash():
    """Slug starting with dash is rejected."""
    from arclane.provisioning.deploy import _validate_slug

    with pytest.raises(ValueError):
        _validate_slug("-my-business")


# --- Existing container replacement ---


async def test_deploy_replaces_existing_container(db, mock_docker, tmp_path):
    """Deploying stops and removes existing container first."""
    from arclane.provisioning import deploy

    template_dir = tmp_path / "replace"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    old_container = MagicMock()
    mock_docker.containers.get.return_value = old_container

    deploy._docker_available = True
    deploy._docker_client = mock_docker

    try:
        with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
             patch.object(deploy, "WORKSPACES_DIR", tmp_path / "ws"), \
             patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
            async with db() as session:
                await deploy.deploy_template("replace-biz", "replace", session)

        old_container.stop.assert_called_once_with(timeout=10)
        old_container.remove.assert_called_once()
    finally:
        deploy._docker_available = None
        deploy._docker_client = None


# --- Simulated deployment ---


async def test_simulated_deploy_returns_no_container_id(db, tmp_path):
    """Simulated deployment returns None for container_id."""
    from arclane.provisioning import deploy

    template_dir = tmp_path / "sim"
    template_dir.mkdir()
    (template_dir / "Dockerfile").write_text("FROM node:18\n")

    deploy._docker_available = False
    deploy._docker_client = None

    with patch.object(deploy, "TEMPLATES_DIR", tmp_path), \
         patch.object(deploy, "WORKSPACES_DIR", tmp_path / "ws"), \
         patch("arclane.provisioning.deploy.update_subdomain_upstream", new_callable=AsyncMock):
        async with db() as session:
            port, container_id = await deploy.deploy_template("sim-biz", "sim", session)

    assert container_id is None
    assert port >= 9000
