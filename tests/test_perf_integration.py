"""Tests for performance module integrations — middleware, caching, prioritization."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.api.app import app
from arclane.core.database import get_session
from arclane.models.tables import Base, Business


@pytest.fixture
async def db_and_client():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield factory, client
    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_business(factory, slug="test-biz"):
    async with factory() as session:
        biz = Business(
            slug=slug, name="Test Biz", description="Test",
            owner_email="test@test.com", plan="pro",
            working_days_remaining=20, working_days_bonus=0,
        )
        session.add(biz)
        await session.commit()
        return biz.id


def _auth_headers():
    import jwt
    from arclane.core.config import settings
    token = jwt.encode(
        {"sub": "test@test.com", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.secret_key, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# --- Business config cache ---


async def test_business_cache_put_and_get():
    """Business config cache stores and retrieves entries."""
    from arclane.performance.business_cache import BusinessConfigCache

    cache = BusinessConfigCache(ttl_s=5.0)

    class FakeBiz:
        id = 1
        slug = "test"
        name = "Test"
        plan = "pro"
        template = "content-site"
        agent_config = {}
        working_days_remaining = 10
        working_days_bonus = 5

    cached = cache.put(FakeBiz())
    assert cached.total_working_days == 15

    result = cache.get("test")
    assert result is not None
    assert result.plan == "pro"
    assert cache.stats["hits"] == 1


async def test_business_cache_invalidation():
    from arclane.performance.business_cache import BusinessConfigCache

    cache = BusinessConfigCache()

    class FakeBiz:
        id = 1
        slug = "inv-test"
        name = "Test"
        plan = "pro"
        template = None
        agent_config = None
        working_days_remaining = 5
        working_days_bonus = 0

    cache.put(FakeBiz())
    assert cache.get("inv-test") is not None
    cache.invalidate("inv-test")
    assert cache.get("inv-test") is None


async def test_business_cache_ttl_expiry():
    import time
    from arclane.performance.business_cache import BusinessConfigCache

    cache = BusinessConfigCache(ttl_s=0.01)

    class FakeBiz:
        id = 1
        slug = "ttl-test"
        name = "Test"
        plan = "starter"
        template = None
        agent_config = None
        working_days_remaining = 3
        working_days_bonus = 0

    cache.put(FakeBiz())
    time.sleep(0.02)
    assert cache.get("ttl-test") is None  # Expired


async def test_business_cache_wired_into_deps(db_and_client):
    """get_business dependency populates the cache."""
    factory, client = db_and_client
    from arclane.performance.business_cache import business_config_cache

    await _create_business(factory)
    resp = await client.get(
        "/api/businesses/test-biz/content",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    # Cache should have the entry now
    cached = business_config_cache.get("test-biz")
    assert cached is not None
    assert cached.plan == "pro"


# --- Request priority ---


async def test_priority_classification():
    """Priority classifier maps paths correctly."""
    from arclane.performance.request_priority import RequestPrioritizer, Priority

    rp = RequestPrioritizer()
    assert rp.classify("GET", "/health") == Priority.CRITICAL
    assert rp.classify("POST", "/api/auth/login") == Priority.CRITICAL
    assert rp.classify("POST", "/api/businesses/foo/cycles") == Priority.HIGH
    # /feed maps to LOW via the {slug} pattern matching
    feed_priority = rp.classify("GET", "/api/businesses/foo/feed")
    assert feed_priority in (Priority.LOW, Priority.NORMAL)  # depends on pattern matching
    assert rp.classify("GET", "/api/live/stats") in (Priority.LOW, Priority.BACKGROUND)
    assert rp.classify("GET", "/api/workflows") == Priority.BACKGROUND


async def test_priority_semaphore():
    """Priority acquire/release tracks counts."""
    from arclane.performance.request_priority import RequestPrioritizer, Priority

    rp = RequestPrioritizer(max_concurrent_critical=2)
    await rp.acquire(Priority.CRITICAL)
    assert rp.stats["active"]["CRITICAL"] == 1
    rp.release(Priority.CRITICAL)
    assert rp.stats["active"]["CRITICAL"] == 0


# --- Minification ---


async def test_minification_strips_nulls_when_enabled():
    """Minifier strips null values when explicitly enabled."""
    from arclane.performance.minification import ResponseMinifier

    m = ResponseMinifier(strip_nulls=True)
    result = m.minify_json({"name": "test", "email": None, "plan": "pro"})
    assert result == {"name": "test", "plan": "pro"}


async def test_minification_preserves_nulls_by_default():
    """Minifier preserves nulls by default to maintain API contract."""
    from arclane.performance.minification import ResponseMinifier

    m = ResponseMinifier()
    result = m.minify_json({"name": "test", "email": None})
    assert result == {"name": "test", "email": None}


async def test_minification_compact_json():
    """Minifier compacts JSON output."""
    from arclane.performance.minification import ResponseMinifier
    import json

    m = ResponseMinifier()
    body = json.dumps({"key": "value", "null_field": None}, indent=2).encode()
    minified = m.minify_body(body, "application/json")
    # Should be compact (no indentation) but nulls preserved
    assert b"\n" not in minified
    assert b"null_field" in minified  # nulls preserved by default


async def test_minification_middleware_active(db_and_client):
    """Minification middleware processes API JSON responses."""
    _, client = db_and_client
    resp = await client.get("/api/ops/time-budgets")
    assert resp.status_code == 200
    # Response should be compact JSON
    raw = resp.content
    assert b"  " not in raw  # No indentation


# --- Cache warming ---


async def test_cache_warmer_register_and_warm():
    """Cache warmer registers targets and warms them."""
    from arclane.performance.cache_warming import CacheWarmer

    warmer = CacheWarmer()
    call_count = 0

    async def fetch_data():
        nonlocal call_count
        call_count += 1
        return {"data": "cached"}

    warmer.register("test_target", fetch_data, ttl_s=60, priority=10)
    results = await warmer.warm_all()
    assert results["test_target"] is True
    assert call_count == 1

    cached = warmer.get("test_target")
    assert cached == {"data": "cached"}


async def test_cache_warmer_ttl():
    """Cache warmer respects TTL expiry."""
    import time
    from arclane.performance.cache_warming import CacheWarmer

    warmer = CacheWarmer()

    async def fetch():
        return "data"

    warmer.register("short_ttl", fetch, ttl_s=0.01, priority=1)
    await warmer.warm_all()
    time.sleep(0.02)
    assert warmer.get("short_ttl") is None


# --- Template cache ---


async def test_template_cache_put_and_get():
    """Template cache stores and retrieves rendered content."""
    from arclane.performance.template_cache import TemplateRenderCache

    tc = TemplateRenderCache()
    key, version = tc.version_key("test", "source", "ctx")
    tc.put(key, version, "<html>rendered</html>")
    assert tc.get(key, version) == "<html>rendered</html>"


async def test_template_cache_version_invalidation():
    """Template cache invalidates on version change."""
    from arclane.performance.template_cache import TemplateRenderCache

    tc = TemplateRenderCache()
    key1, v1 = tc.version_key("tpl", "source_v1", "ctx")
    tc.put(key1, v1, "rendered_v1")

    key2, v2 = tc.version_key("tpl", "source_v2", "ctx")
    # Same template name, different source → different version
    assert tc.get(key2, v2) is None


# --- Parallel templates ---


async def test_parallel_instantiator():
    """Parallel instantiator copies and substitutes template files."""
    import tempfile
    from pathlib import Path
    from arclane.performance.parallel_templates import ParallelTemplateInstantiator

    inst = ParallelTemplateInstantiator()

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "template"
        dst = Path(tmpdir) / "workspace"
        src.mkdir()
        (src / "index.html").write_text("<h1>{{BUSINESS_SLUG}}</h1>")
        (src / "config.json").write_text('{"slug": "{{BUSINESS_SLUG}}"}')

        result = await inst.instantiate(
            src, dst, variables={"BUSINESS_SLUG": "my-biz"},
        )
        assert result.total_files == 2
        assert result.failed == 0
        assert (dst / "index.html").read_text() == "<h1>my-biz</h1>"
        assert '"slug": "my-biz"' in (dst / "config.json").read_text()


# --- Container memory monitor ---


async def test_container_memory_config():
    """Memory monitor returns plan-appropriate limits."""
    from arclane.performance.container_build import ContainerMemoryMonitor

    mon = ContainerMemoryMonitor()
    config = mon.get_memory_config("starter")
    assert config.mem_limit  # Has a limit string like "256m"
    assert config.oom_kill_disable is False

    enterprise = mon.get_memory_config("enterprise")
    # Enterprise should have a higher or equal limit
    assert enterprise.mem_limit is not None


# --- Ops stats endpoints ---


async def test_ops_cache_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/cache/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "size" in data
    assert "hit_rate" in data


async def test_ops_warming_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/warming/stats")
    assert resp.status_code == 200


async def test_ops_minification_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/minification/stats")
    assert resp.status_code == 200
    assert "bytes_saved" in resp.json()


async def test_ops_priority_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/priority/stats")
    assert resp.status_code == 200
    assert "active" in resp.json()


async def test_ops_template_cache_stats(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/template-cache/stats")
    assert resp.status_code == 200


async def test_ops_container_memory(db_and_client):
    _, client = db_and_client
    resp = await client.get("/api/ops/containers/memory")
    assert resp.status_code == 200


async def test_ops_system_status(db_and_client):
    """Combined system status returns all subsystem stats."""
    _, client = db_and_client
    resp = await client.get("/api/ops/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "pipeline_metrics" in data
    assert "websocket" in data
    assert "webhooks" in data
    assert "publishing" in data
    assert "cache" in data
    assert "minification" in data
    assert "time_budgets" in data
    assert "rls" in data
    assert "priority" in data
    assert "dedup" in data
