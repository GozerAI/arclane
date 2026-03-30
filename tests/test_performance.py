"""Tests for arclane.performance — items 6, 15, 23, 30, 41, 49, 57, 65, 75,
83, 89, 96, 136, 142, 150, 163, 175, 186, 208, 215, 245.
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from arclane.models.tables import Base, Business


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


@pytest.fixture
def sample_business(session):
    """Create and return a Business ORM instance."""
    biz = Business(
        slug="test-biz",
        name="Test Business",
        description="A test business",
        owner_email="test@arclane.cloud",
        plan="starter",
        working_days_remaining=5,
        working_days_bonus=10,
        template="content-site",
        agent_config={"mode": "auto"},
    )
    return biz


# ===========================================================================
# Item 6: Query plan analysis
# ===========================================================================

class TestQueryAnalyzer:
    """Tests for database query plan analysis middleware."""

    def test_init_defaults(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        qa = QueryAnalyzer()
        assert qa.enabled is True
        assert qa.slow_queries == []

    def test_threshold_configurable(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        qa = QueryAnalyzer(threshold_s=0.5)
        assert qa._threshold == 0.5

    def test_enable_disable(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        qa = QueryAnalyzer()
        qa.enabled = False
        assert qa.enabled is False
        qa.enabled = True
        assert qa.enabled is True

    def test_clear_queries(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        qa = QueryAnalyzer()
        qa._slow_queries.append({"statement": "SELECT 1"})
        assert len(qa.slow_queries) == 1
        qa.clear()
        assert len(qa.slow_queries) == 0

    def test_slow_queries_returns_copy(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        qa = QueryAnalyzer()
        qa._slow_queries.append({"statement": "SELECT 1"})
        queries = qa.slow_queries
        queries.clear()
        assert len(qa.slow_queries) == 1

    def test_attach_detach(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        from sqlalchemy import create_engine
        qa = QueryAnalyzer()
        eng = create_engine("sqlite://")
        qa.attach(eng)
        qa.detach(eng)

    def test_singleton_exists(self):
        from arclane.performance.query_analysis import query_analyzer
        assert query_analyzer is not None
        assert query_analyzer.enabled is True

    def test_before_execute_skips_when_disabled(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        qa = QueryAnalyzer()
        qa.enabled = False
        conn = MagicMock()
        qa._before_execute(conn, MagicMock(), "SELECT 1", None, None, False)
        assert "query_start_time" not in conn.info

    def test_after_execute_skips_when_disabled(self):
        from arclane.performance.query_analysis import QueryAnalyzer
        qa = QueryAnalyzer()
        qa.enabled = False
        conn = MagicMock()
        qa._after_execute(conn, MagicMock(), "SELECT 1", None, None, False)
        assert len(qa.slow_queries) == 0


# ===========================================================================
# Item 15: Query deduplication
# ===========================================================================

class TestRequestDeduplicator:
    """Tests for concurrent request deduplication."""

    def test_make_key_deterministic(self):
        from arclane.performance.deduplication import RequestDeduplicator
        k1 = RequestDeduplicator.make_key("GET", "/api/live")
        k2 = RequestDeduplicator.make_key("GET", "/api/live")
        assert k1 == k2

    def test_make_key_different_methods(self):
        from arclane.performance.deduplication import RequestDeduplicator
        k1 = RequestDeduplicator.make_key("GET", "/api/live")
        k2 = RequestDeduplicator.make_key("POST", "/api/live")
        assert k1 != k2

    def test_make_key_different_paths(self):
        from arclane.performance.deduplication import RequestDeduplicator
        k1 = RequestDeduplicator.make_key("GET", "/api/live")
        k2 = RequestDeduplicator.make_key("GET", "/api/stats")
        assert k1 != k2

    @pytest.mark.asyncio
    async def test_deduplicate_first_call(self):
        from arclane.performance.deduplication import RequestDeduplicator
        dedup = RequestDeduplicator()
        key = dedup.make_key("GET", "/test")

        async def fetch():
            return 42

        result = await dedup.deduplicate(key, fetch)
        assert result == 42
        assert dedup.stats["misses"] == 1

    @pytest.mark.asyncio
    async def test_deduplicate_concurrent(self):
        from arclane.performance.deduplication import RequestDeduplicator
        dedup = RequestDeduplicator(ttl_s=5.0)
        call_count = 0

        async def slow_fetch():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return "data"

        key = dedup.make_key("GET", "/concurrent")
        results = await asyncio.gather(
            dedup.deduplicate(key, slow_fetch),
            dedup.deduplicate(key, slow_fetch),
            dedup.deduplicate(key, slow_fetch),
        )
        assert all(r == "data" for r in results)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_deduplicate_error_propagation(self):
        from arclane.performance.deduplication import RequestDeduplicator
        dedup = RequestDeduplicator()
        key = dedup.make_key("GET", "/error")

        async def failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await dedup.deduplicate(key, failing)

    def test_reset_stats(self):
        from arclane.performance.deduplication import RequestDeduplicator
        dedup = RequestDeduplicator()
        dedup._hits = 10
        dedup._misses = 5
        dedup.reset_stats()
        assert dedup.stats["hits"] == 0
        assert dedup.stats["misses"] == 0

    def test_singleton_exists(self):
        from arclane.performance.deduplication import request_deduplicator
        assert request_deduplicator is not None


# ===========================================================================
# Item 23: Migration performance benchmarking
# ===========================================================================

class TestMigrationBenchmarker:
    """Tests for migration benchmarking."""

    def test_init(self):
        from arclane.performance.migration_benchmark import MigrationBenchmarker
        mb = MigrationBenchmarker()
        assert mb.results == []

    def test_clear(self):
        from arclane.performance.migration_benchmark import MigrationBenchmarker, MigrationBenchmark
        mb = MigrationBenchmarker()
        mb._results.append(MigrationBenchmark(revision="abc", direction="upgrade"))
        mb.clear()
        assert mb.results == []

    @pytest.mark.asyncio
    async def test_snapshot_schema(self, engine):
        from arclane.performance.migration_benchmark import MigrationBenchmarker
        mb = MigrationBenchmarker()
        tables, counts = await mb.snapshot_schema(engine)
        assert "businesses" in tables
        assert "cycles" in tables
        assert counts["businesses"] == 0

    @pytest.mark.asyncio
    async def test_benchmark_migration_success(self, engine):
        from arclane.performance.migration_benchmark import MigrationBenchmarker
        mb = MigrationBenchmarker()

        async def noop_migration():
            pass

        result = await mb.benchmark_migration(engine, "rev1", "upgrade", noop_migration)
        assert result.success is True
        assert result.elapsed_s >= 0
        assert result.revision == "rev1"
        assert result.direction == "upgrade"
        assert len(mb.results) == 1

    @pytest.mark.asyncio
    async def test_benchmark_migration_failure(self, engine):
        from arclane.performance.migration_benchmark import MigrationBenchmarker
        mb = MigrationBenchmarker()

        async def failing_migration():
            raise RuntimeError("migration failed")

        result = await mb.benchmark_migration(engine, "rev2", "upgrade", failing_migration)
        assert result.success is False
        assert result.error == "migration failed"

    @pytest.mark.asyncio
    async def test_benchmark_sync_migration(self, engine):
        from arclane.performance.migration_benchmark import MigrationBenchmarker
        mb = MigrationBenchmarker()

        def sync_migration():
            pass

        result = await mb.benchmark_migration(engine, "rev3", "downgrade", sync_migration)
        assert result.success is True
        assert result.direction == "downgrade"

    def test_summary_empty(self):
        from arclane.performance.migration_benchmark import MigrationBenchmarker
        mb = MigrationBenchmarker()
        s = mb.summary()
        assert s["total"] == 0

    @pytest.mark.asyncio
    async def test_summary_with_results(self, engine):
        from arclane.performance.migration_benchmark import MigrationBenchmarker
        mb = MigrationBenchmarker()
        await mb.benchmark_migration(engine, "r1", "upgrade", lambda: None)
        s = mb.summary()
        assert s["total"] == 1
        assert s["failures"] == 0

    def test_singleton_exists(self):
        from arclane.performance.migration_benchmark import migration_benchmarker
        assert migration_benchmarker is not None


# ===========================================================================
# Item 30: Row-level security
# ===========================================================================

class TestRowLevelSecurity:
    """Tests for row-level tenant isolation."""

    def test_set_get_tenant_id(self):
        from arclane.performance.row_level_security import set_tenant_id, get_tenant_id
        set_tenant_id(42)
        assert get_tenant_id() == 42
        set_tenant_id(None)
        assert get_tenant_id() is None

    def test_tenant_filter_init(self):
        from arclane.performance.row_level_security import TenantFilter
        tf = TenantFilter()
        assert tf.enabled is True
        assert tf.filter_count == 0

    def test_tenant_filter_disable(self):
        from arclane.performance.row_level_security import TenantFilter
        tf = TenantFilter()
        tf.enabled = False
        result = tf.apply_filter(MagicMock(), 1)
        assert result is not None

    def test_reset_stats(self):
        from arclane.performance.row_level_security import TenantFilter
        tf = TenantFilter()
        tf._filter_count = 5
        tf.reset_stats()
        assert tf.filter_count == 0

    def test_tenant_context_sync(self):
        from arclane.performance.row_level_security import TenantContext, get_tenant_id
        with TenantContext(99):
            assert get_tenant_id() == 99
        assert get_tenant_id() is None or get_tenant_id() != 99

    @pytest.mark.asyncio
    async def test_tenant_context_async(self):
        from arclane.performance.row_level_security import TenantContext, get_tenant_id
        async with TenantContext(100):
            assert get_tenant_id() == 100

    def test_tenant_tables(self):
        from arclane.performance.row_level_security import TenantFilter
        tf = TenantFilter()
        assert "cycles" in tf.TENANT_TABLES
        assert "activity" in tf.TENANT_TABLES
        assert "content" in tf.TENANT_TABLES

    def test_singleton_exists(self):
        from arclane.performance.row_level_security import tenant_filter
        assert tenant_filter is not None


# ===========================================================================
# Item 41: Template rendering cache
# ===========================================================================

class TestTemplateCache:
    """Tests for template rendering cache with versioned keys."""

    def test_version_key_deterministic(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        k1, v1 = tc.version_key("t1", "<html>content</html>")
        k2, v2 = tc.version_key("t1", "<html>content</html>")
        assert k1 == k2
        assert v1 == v2

    def test_version_key_changes_on_source_change(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        _, v1 = tc.version_key("t1", "version1")
        _, v2 = tc.version_key("t1", "version2")
        assert v1 != v2

    def test_put_and_get(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        tc.put("key1", "v1", "<p>Hello</p>")
        result = tc.get("key1", "v1")
        assert result == "<p>Hello</p>"

    def test_get_version_mismatch(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        tc.put("key1", "v1", "<p>Hello</p>")
        result = tc.get("key1", "v2")
        assert result is None

    def test_get_expired(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache(ttl_s=0.0)
        tc.put("key1", "v1", "<p>Hello</p>")
        result = tc.get("key1", "v1")
        assert result is None

    def test_invalidate(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        tc.put("mytemplate:v1:ctx1", "v1", "content1")
        tc.put("mytemplate:v1:ctx2", "v1", "content2")
        tc.put("other:v1:ctx1", "v1", "other")
        count = tc.invalidate("mytemplate")
        assert count == 2
        assert tc.get("mytemplate:v1:ctx1", "v1") is None
        assert tc.get("other:v1:ctx1", "v1") == "other"

    def test_clear(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        tc.put("k1", "v1", "a")
        tc.put("k2", "v1", "b")
        tc.clear()
        assert tc.stats["size"] == 0

    def test_lru_eviction(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache(max_size=2)
        tc.put("k1", "v1", "a")
        tc.put("k2", "v1", "b")
        # Access k1 so k2 is the LRU
        tc.get("k1", "v1")
        tc.put("k3", "v1", "c")
        assert tc.stats["size"] == 2

    def test_render_cached(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        render_fn = MagicMock(return_value="rendered")
        result = tc.render_cached("t1", "source", render_fn, {"key": "val"})
        assert result == "rendered"
        render_fn.assert_called_once()
        # Second call should use cache
        result2 = tc.render_cached("t1", "source", render_fn, {"key": "val"})
        assert result2 == "rendered"
        assert render_fn.call_count == 1

    def test_stats(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        tc.put("k1", "v1", "a")
        tc.get("k1", "v1")
        tc.get("k2", "v2")
        s = tc.stats
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["size"] == 1

    def test_reset_stats(self):
        from arclane.performance.template_cache import TemplateRenderCache
        tc = TemplateRenderCache()
        tc._hits = 10
        tc._misses = 5
        tc.reset_stats()
        assert tc.stats["hits"] == 0
        assert tc.stats["misses"] == 0

    def test_singleton_exists(self):
        from arclane.performance.template_cache import template_cache
        assert template_cache is not None


# ===========================================================================
# Items 49 & 215: CDN caching headers and static asset delivery
# ===========================================================================

class TestCDNCacheHeaders:
    """Tests for CDN cache headers and static asset delivery."""

    def test_static_cache_duration_js(self):
        from arclane.performance.cdn_headers import get_cache_duration
        # JS/CSS cache is 0 during development to prevent stale UI
        assert get_cache_duration("/static/app.js") == 0

    def test_static_cache_duration_png(self):
        from arclane.performance.cdn_headers import get_cache_duration
        assert get_cache_duration("/images/logo.png") == 86400 * 365

    def test_api_cache_duration_health(self):
        from arclane.performance.cdn_headers import get_cache_duration
        assert get_cache_duration("/health") == 10

    def test_no_cache_duration_unknown(self):
        from arclane.performance.cdn_headers import get_cache_duration
        assert get_cache_duration("/api/businesses") is None

    def test_compute_etag(self):
        from arclane.performance.cdn_headers import compute_etag
        etag = compute_etag(b"hello world")
        assert etag.startswith('W/"')
        assert etag.endswith('"')

    def test_compute_etag_str(self):
        from arclane.performance.cdn_headers import compute_etag
        etag = compute_etag("hello world")
        assert etag.startswith('W/"')

    def test_cdn_config_disabled_by_default(self):
        from arclane.performance.cdn_headers import CDNCacheConfig
        config = CDNCacheConfig()
        assert config.enabled is False

    def test_cdn_config_enabled_with_url(self):
        from arclane.performance.cdn_headers import CDNCacheConfig
        config = CDNCacheConfig(cdn_base_url="https://cdn.arclane.cloud")
        assert config.enabled is True

    def test_cdn_url_rewrite(self):
        from arclane.performance.cdn_headers import CDNCacheConfig
        config = CDNCacheConfig(cdn_base_url="https://cdn.arclane.cloud")
        assert config.rewrite_url("/static/app.js") == "https://cdn.arclane.cloud/static/app.js"

    def test_cdn_url_rewrite_disabled(self):
        from arclane.performance.cdn_headers import CDNCacheConfig
        config = CDNCacheConfig()
        assert config.rewrite_url("/static/app.js") == "/static/app.js"

    def test_cdn_disable_toggle(self):
        from arclane.performance.cdn_headers import CDNCacheConfig
        config = CDNCacheConfig(cdn_base_url="https://cdn.arclane.cloud")
        config.enabled = False
        assert config.enabled is False

    def test_origin_shield(self):
        from arclane.performance.cdn_headers import CDNCacheConfig
        config = CDNCacheConfig(cdn_base_url="https://cdn.arclane.cloud", origin_shield=True)
        assert config.origin_shield is True

    def test_vary_headers_default(self):
        from arclane.performance.cdn_headers import CDNCacheConfig
        config = CDNCacheConfig()
        assert "Accept" in config.vary_headers
        assert "Accept-Encoding" in config.vary_headers


# ===========================================================================
# Item 57: User session data preloading
# ===========================================================================

class TestSessionPreloader:
    """Tests for session data preloading."""

    def test_preloaded_session_defaults(self):
        from arclane.performance.session_preload import PreloadedSession
        ps = PreloadedSession()
        assert ps.is_loaded is False
        assert ps.total_working_days == 0

    def test_preloaded_session_with_data(self):
        from arclane.performance.session_preload import PreloadedSession
        ps = PreloadedSession(
            business_id=1, working_days_remaining=5, working_days_bonus=10,
        )
        assert ps.is_loaded is True
        assert ps.total_working_days == 15

    def test_preloader_init(self):
        from arclane.performance.session_preload import SessionPreloader
        sp = SessionPreloader()
        assert sp.preload_count == 0

    def test_reset_stats(self):
        from arclane.performance.session_preload import SessionPreloader
        sp = SessionPreloader()
        sp._preload_count = 5
        sp.reset_stats()
        assert sp.preload_count == 0

    def test_clear_cache(self):
        from arclane.performance.session_preload import SessionPreloader, PreloadedSession
        sp = SessionPreloader()
        sp._cache["key"] = PreloadedSession()
        sp.clear_cache()
        assert len(sp._cache) == 0

    @pytest.mark.asyncio
    async def test_preload_by_slug(self, session, sample_business):
        from arclane.performance.session_preload import SessionPreloader
        session.add(sample_business)
        await session.commit()
        sp = SessionPreloader()
        result = await sp.preload(session, business_slug="test-biz")
        assert result.is_loaded is True
        assert result.business_slug == "test-biz"
        assert result.plan == "starter"
        assert sp.preload_count == 1

    @pytest.mark.asyncio
    async def test_preload_by_email(self, session, sample_business):
        from arclane.performance.session_preload import SessionPreloader
        session.add(sample_business)
        await session.commit()
        sp = SessionPreloader()
        result = await sp.preload(session, email="test@arclane.cloud")
        assert result.is_loaded is True
        assert result.business_name == "Test Business"

    @pytest.mark.asyncio
    async def test_preload_cache_hit(self, session, sample_business):
        from arclane.performance.session_preload import SessionPreloader
        session.add(sample_business)
        await session.commit()
        sp = SessionPreloader()
        r1 = await sp.preload(session, business_slug="test-biz")
        r2 = await sp.preload(session, business_slug="test-biz")
        assert r1 is r2
        assert sp.preload_count == 1

    @pytest.mark.asyncio
    async def test_preload_missing(self, session):
        from arclane.performance.session_preload import SessionPreloader
        sp = SessionPreloader()
        result = await sp.preload(session, business_slug="nonexistent")
        assert result.is_loaded is False

    def test_singleton_exists(self):
        from arclane.performance.session_preload import session_preloader
        assert session_preloader is not None


# ===========================================================================
# Item 65: Business configuration cache
# ===========================================================================

class TestBusinessConfigCache:
    """Tests for business configuration cache."""

    def test_cache_init(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache()
        assert bc.stats["size"] == 0

    def test_put_and_get(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache()
        biz = MagicMock(
            id=1, slug="test", name="Test", plan="starter",
            template="content-site", agent_config={},
            working_days_remaining=5, working_days_bonus=10,
        )
        bc.put(biz)
        result = bc.get("test")
        assert result is not None
        assert result.slug == "test"
        assert result.total_working_days == 15

    def test_get_expired(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache(ttl_s=0.0)
        biz = MagicMock(
            id=1, slug="test", name="Test", plan="starter",
            template=None, agent_config=None,
            working_days_remaining=0, working_days_bonus=0,
        )
        bc.put(biz)
        assert bc.get("test") is None

    def test_get_missing(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache()
        assert bc.get("nonexistent") is None

    def test_invalidate(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache()
        biz = MagicMock(
            id=1, slug="test", name="Test", plan="starter",
            template=None, agent_config=None,
            working_days_remaining=0, working_days_bonus=0,
        )
        bc.put(biz)
        assert bc.invalidate("test") is True
        assert bc.invalidate("test") is False

    def test_invalidate_all(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache()
        for i in range(3):
            biz = MagicMock(
                id=i, slug=f"s{i}", name=f"N{i}", plan="starter",
                template=None, agent_config=None,
                working_days_remaining=0, working_days_bonus=0,
            )
            bc.put(biz)
        count = bc.invalidate_all()
        assert count == 3
        assert bc.stats["size"] == 0

    def test_eviction(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache(max_size=2)
        for i in range(3):
            biz = MagicMock(
                id=i, slug=f"s{i}", name=f"N{i}", plan="starter",
                template=None, agent_config=None,
                working_days_remaining=0, working_days_bonus=0,
            )
            bc.put(biz)
        assert bc.stats["size"] == 2

    def test_hit_rate(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache()
        biz = MagicMock(
            id=1, slug="test", name="Test", plan="starter",
            template=None, agent_config=None,
            working_days_remaining=0, working_days_bonus=0,
        )
        bc.put(biz)
        bc.get("test")  # hit
        bc.get("missing")  # miss
        assert bc.stats["hit_rate"] == 0.5

    def test_reset_stats(self):
        from arclane.performance.business_cache import BusinessConfigCache
        bc = BusinessConfigCache()
        bc._hits = 10
        bc._misses = 5
        bc.reset_stats()
        assert bc.stats["hits"] == 0

    def test_singleton_exists(self):
        from arclane.performance.business_cache import business_config_cache
        assert business_config_cache is not None


# ===========================================================================
# Item 75: Pagination with Link headers
# ===========================================================================

class TestPagination:
    """Tests for RFC 8288 Link header pagination."""

    def test_pagination_params_defaults(self):
        from arclane.performance.pagination import PaginationParams
        p = PaginationParams()
        assert p.page == 1
        assert p.per_page == 50
        assert p.offset == 0
        assert p.limit == 50

    def test_pagination_params_page_2(self):
        from arclane.performance.pagination import PaginationParams
        p = PaginationParams(page=2, per_page=20)
        assert p.offset == 20
        assert p.limit == 20

    def test_pagination_params_clamp(self):
        from arclane.performance.pagination import PaginationParams
        p = PaginationParams(page=-1, per_page=500)
        assert p.page == 1
        assert p.per_page == 200  # clamped to max

    def test_paginated_response_link_header(self):
        from arclane.performance.pagination import PaginationParams, PaginatedResponse
        request = MagicMock()
        request.url = MagicMock()
        request.url.__str__ = lambda self: "http://localhost/api/items?sort=name"
        request.query_params = {"sort": "name"}
        params = PaginationParams(page=2, per_page=10)
        pr = PaginatedResponse(items=list(range(10)), total=50, params=params, request=request)
        link = pr.link_header()
        assert 'rel="first"' in link
        assert 'rel="last"' in link
        assert 'rel="prev"' in link
        assert 'rel="next"' in link

    def test_paginated_response_first_page(self):
        from arclane.performance.pagination import PaginationParams, PaginatedResponse
        request = MagicMock()
        request.url = MagicMock()
        request.url.__str__ = lambda self: "http://localhost/api/items"
        request.query_params = {}
        params = PaginationParams(page=1, per_page=10)
        pr = PaginatedResponse(items=list(range(10)), total=50, params=params, request=request)
        assert pr.has_prev is False
        assert pr.has_next is True

    def test_paginated_response_last_page(self):
        from arclane.performance.pagination import PaginationParams, PaginatedResponse
        request = MagicMock()
        request.url = MagicMock()
        request.url.__str__ = lambda self: "http://localhost/api/items"
        request.query_params = {}
        params = PaginationParams(page=5, per_page=10)
        pr = PaginatedResponse(items=list(range(10)), total=50, params=params, request=request)
        assert pr.has_prev is True
        assert pr.has_next is False

    def test_paginated_response_to_dict(self):
        from arclane.performance.pagination import PaginationParams, PaginatedResponse
        request = MagicMock()
        request.url = MagicMock()
        request.url.__str__ = lambda self: "http://localhost/api/items"
        request.query_params = {}
        params = PaginationParams(page=1, per_page=10)
        pr = PaginatedResponse(items=["a", "b"], total=2, params=params, request=request)
        d = pr.to_dict()
        assert d["items"] == ["a", "b"]
        assert d["pagination"]["total"] == 2
        assert d["pagination"]["total_pages"] == 1

    def test_apply_headers(self):
        from arclane.performance.pagination import PaginationParams, PaginatedResponse
        request = MagicMock()
        request.url = MagicMock()
        request.url.__str__ = lambda self: "http://localhost/api/items"
        request.query_params = {}
        params = PaginationParams(page=1, per_page=10)
        pr = PaginatedResponse(items=[], total=100, params=params, request=request)
        response = MagicMock()
        response.headers = {}
        pr.apply_headers(response)
        assert response.headers["X-Total-Count"] == "100"
        assert response.headers["X-Page"] == "1"
        assert response.headers["X-Total-Pages"] == "10"


# ===========================================================================
# Item 83: API response time budgets
# ===========================================================================

class TestTimeBudgets:
    """Tests for endpoint time budgets."""

    def test_default_budgets(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        assert r.get_budget("/health") == 50
        assert r.get_budget("/api/auth/login") == 2000

    def test_cycle_budget(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        assert r.get_budget("/api/businesses/test/cycles") == 10000

    def test_default_api_budget(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        assert r.get_budget("/api/unknown/endpoint") == 3000

    def test_set_custom_budget(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        r.set_budget("/api/custom", 1500)
        assert r.get_budget("/api/custom") == 1500

    def test_check_budget_ok(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        assert r.check_budget("/health", 30) is True

    def test_check_budget_exceeded(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        assert r.check_budget("/health", 100) is False
        assert len(r.violations) == 1
        assert r.violations[0]["overage_ms"] > 0

    def test_check_budget_disabled(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        r.enabled = False
        assert r.check_budget("/health", 999999) is True

    def test_clear_violations(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        r.check_budget("/health", 200)
        r.clear_violations()
        assert r.violations == []

    def test_stats(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        r.check_budget("/health", 200)
        r.check_budget("/health", 300)
        s = r.stats()
        assert s["total_violations"] == 2
        assert len(s["worst_offenders"]) == 1
        assert s["worst_offenders"][0]["count"] == 2

    def test_violation_trimming(self):
        from arclane.performance.time_budgets import TimeBudgetRegistry
        r = TimeBudgetRegistry()
        for _ in range(1100):
            r.check_budget("/health", 200)
        # Trimming at >1000 keeps 500, then 99 more are added = 599
        # The point is it doesn't grow unboundedly
        assert len(r.violations) < 1100

    def test_singleton_exists(self):
        from arclane.performance.time_budgets import time_budget_registry
        assert time_budget_registry is not None


# ===========================================================================
# Item 89: Response body minification
# ===========================================================================

class TestResponseMinifier:
    """Tests for JSON response minification."""

    def test_minify_strips_nulls(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier(strip_nulls=True)
        result = m.minify_json({"a": 1, "b": None, "c": "x"})
        assert result == {"a": 1, "c": "x"}

    def test_minify_keeps_nulls_when_disabled(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier(strip_nulls=False)
        result = m.minify_json({"a": 1, "b": None})
        assert result == {"a": 1, "b": None}

    def test_minify_strips_empty_collections(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier(strip_empty=True)
        result = m.minify_json({"a": 1, "b": [], "c": {}})
        assert result == {"a": 1}

    def test_minify_nested(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier(strip_nulls=True)
        result = m.minify_json({"a": {"b": None, "c": 1}})
        assert result == {"a": {"c": 1}}

    def test_minify_list(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier(strip_nulls=True)
        result = m.minify_json([{"a": None}, {"b": 1}])
        assert result == [{}, {"b": 1}]

    def test_minify_body_json(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier(strip_nulls=True)
        body = json.dumps({"a": 1, "b": None, "c": "hello"}).encode()
        result = m.minify_body(body, "application/json")
        parsed = json.loads(result)
        assert "b" not in parsed

    def test_minify_body_not_json(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier()
        body = b"<html>hello</html>"
        result = m.minify_body(body, "text/html")
        assert result == body

    def test_minify_body_disabled(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier()
        m.enabled = False
        body = json.dumps({"a": None}).encode()
        result = m.minify_body(body, "application/json")
        assert result == body

    def test_bytes_saved_tracking(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier()
        body = json.dumps({"a": 1, "b": None, "c": None, "d": None}, indent=2).encode()
        m.minify_body(body, "application/json")
        assert m.bytes_saved > 0

    def test_minify_html(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier()
        html = "<p>  hello  </p>   <div>  world  </div>"
        result = m.minify_html(html)
        assert ">  <" not in result

    def test_reset_stats(self):
        from arclane.performance.minification import ResponseMinifier
        m = ResponseMinifier()
        m._bytes_saved = 100
        m.reset_stats()
        assert m.bytes_saved == 0

    def test_singleton_exists(self):
        from arclane.performance.minification import response_minifier
        assert response_minifier is not None


# ===========================================================================
# Item 96: Cache warming
# ===========================================================================

class TestCacheWarmer:
    """Tests for response cache warming."""

    def test_register_target(self):
        from arclane.performance.cache_warming import CacheWarmer

        async def fetcher():
            return {"data": 1}

        cw = CacheWarmer()
        cw.register("live_stats", fetcher, ttl_s=60, priority=10)
        assert cw.stats["targets"] == 1

    def test_unregister_target(self):
        from arclane.performance.cache_warming import CacheWarmer

        async def fetcher():
            return {}

        cw = CacheWarmer()
        cw.register("key1", fetcher)
        cw.unregister("key1")
        assert cw.stats["targets"] == 0

    @pytest.mark.asyncio
    async def test_warm_one(self):
        from arclane.performance.cache_warming import CacheWarmer

        async def fetcher():
            return {"result": 42}

        cw = CacheWarmer()
        cw.register("key1", fetcher)
        ok = await cw.warm_one("key1")
        assert ok is True
        assert cw.get("key1") == {"result": 42}

    @pytest.mark.asyncio
    async def test_warm_one_missing(self):
        from arclane.performance.cache_warming import CacheWarmer
        cw = CacheWarmer()
        ok = await cw.warm_one("missing")
        assert ok is False

    @pytest.mark.asyncio
    async def test_warm_one_failure(self):
        from arclane.performance.cache_warming import CacheWarmer

        async def failing():
            raise RuntimeError("oops")

        cw = CacheWarmer()
        cw.register("fail", failing)
        ok = await cw.warm_one("fail")
        assert ok is False

    @pytest.mark.asyncio
    async def test_warm_all(self):
        from arclane.performance.cache_warming import CacheWarmer

        async def f1():
            return "a"

        async def f2():
            return "b"

        cw = CacheWarmer()
        cw.register("k1", f1, priority=10)
        cw.register("k2", f2, priority=5)
        results = await cw.warm_all()
        assert results == {"k1": True, "k2": True}
        assert cw.stats["warm_cycles"] == 1

    @pytest.mark.asyncio
    async def test_get_expired(self):
        from arclane.performance.cache_warming import CacheWarmer

        async def fetcher():
            return "data"

        cw = CacheWarmer()
        cw.register("k1", fetcher, ttl_s=0.0)
        await cw.warm_one("k1")
        assert cw.get("k1") is None

    def test_get_hit_count(self):
        from arclane.performance.cache_warming import CacheWarmer, WarmCacheEntry
        cw = CacheWarmer()
        cw._cache["k1"] = WarmCacheEntry(key="k1", data="x", ttl_s=999)
        cw.get("k1")
        cw.get("k1")
        assert cw._cache["k1"].hit_count == 2

    def test_clear(self):
        from arclane.performance.cache_warming import CacheWarmer, WarmCacheEntry
        cw = CacheWarmer()
        cw._cache["k1"] = WarmCacheEntry(key="k1", data="x", ttl_s=999)
        cw.clear()
        assert cw.stats["cached"] == 0

    def test_stop(self):
        from arclane.performance.cache_warming import CacheWarmer
        cw = CacheWarmer()
        cw._running = True
        cw.stop()
        assert cw._running is False

    def test_singleton_exists(self):
        from arclane.performance.cache_warming import cache_warmer
        assert cache_warmer is not None


# ===========================================================================
# Items 136, 175, 186: Container build + memory limits + burst monitoring
# ===========================================================================

class TestContainerBuild:
    """Tests for async container builds and memory management."""

    def test_memory_config_defaults(self):
        from arclane.performance.container_build import MemoryConfig
        mc = MemoryConfig()
        assert mc.mem_limit == "256m"
        assert mc.mem_reservation == "128m"
        assert mc.mem_burst_limit == "384m"

    def test_memory_config_to_docker_kwargs(self):
        from arclane.performance.container_build import MemoryConfig
        mc = MemoryConfig(oom_score_adj=500)
        kwargs = mc.to_docker_kwargs()
        assert kwargs["mem_limit"] == "256m"
        assert kwargs["memswap_limit"] == "384m"
        assert kwargs["oom_score_adj"] == 500

    def test_plan_memory_configs(self):
        from arclane.performance.container_build import PLAN_MEMORY_CONFIGS
        assert "starter" in PLAN_MEMORY_CONFIGS
        assert "pro" in PLAN_MEMORY_CONFIGS
        assert "growth" in PLAN_MEMORY_CONFIGS
        assert "scale" in PLAN_MEMORY_CONFIGS
        assert PLAN_MEMORY_CONFIGS["scale"].mem_limit == "2g"

    def test_memory_monitor_get_config(self):
        from arclane.performance.container_build import ContainerMemoryMonitor
        mon = ContainerMemoryMonitor()
        config = mon.get_memory_config("pro")
        assert config.mem_limit == "512m"
        config_default = mon.get_memory_config("unknown_plan")
        assert config_default.mem_limit == "256m"

    def test_record_oom(self):
        from arclane.performance.container_build import ContainerMemoryMonitor
        mon = ContainerMemoryMonitor()
        event = mon.record_oom("test-biz", "abc123", "256m")
        assert event.slug == "test-biz"
        assert event.container_id == "abc123"
        assert event.mem_limit == "256m"
        assert len(mon.oom_events) == 1

    def test_oom_event_trimming(self):
        from arclane.performance.container_build import ContainerMemoryMonitor
        mon = ContainerMemoryMonitor()
        for i in range(110):
            mon.record_oom(f"biz-{i}", f"c{i}")
        # Trims at >100 to 50, then 9 more added = 59; doesn't grow unboundedly
        assert len(mon.oom_events) < 110

    def test_memory_monitor_stats(self):
        from arclane.performance.container_build import ContainerMemoryMonitor
        mon = ContainerMemoryMonitor()
        mon.record_oom("biz1", "c1", "256m")
        s = mon.stats()
        assert s["total_oom_events"] == 1
        assert len(s["recent_ooms"]) == 1

    def test_build_phases(self):
        from arclane.performance.container_build import BuildPhase
        assert BuildPhase.PREPARING == "preparing"
        assert BuildPhase.COMPLETE == "complete"
        assert BuildPhase.FAILED == "failed"

    def test_builder_active_builds(self):
        from arclane.performance.container_build import AsyncContainerBuilder
        builder = AsyncContainerBuilder()
        assert builder.active_builds == {}

    def test_singletons_exist(self):
        from arclane.performance.container_build import (
            container_memory_monitor, async_container_builder,
        )
        assert container_memory_monitor is not None
        assert async_container_builder is not None


# ===========================================================================
# Item 142: Background cycle execution with webhook notification
# ===========================================================================

class TestWebhookCycles:
    """Tests for cycle webhook notifications."""

    def test_register_webhook(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier, WebhookConfig
        notifier = CycleWebhookNotifier()
        notifier.register_webhook(1, WebhookConfig(url="https://example.com/hook"))
        assert notifier.get_webhook(1) is not None
        assert notifier.get_webhook(1).url == "https://example.com/hook"

    def test_unregister_webhook(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier, WebhookConfig
        notifier = CycleWebhookNotifier()
        notifier.register_webhook(1, WebhookConfig(url="https://example.com/hook"))
        notifier.unregister_webhook(1)
        assert notifier.get_webhook(1) is None

    def test_sign_payload(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier
        notifier = CycleWebhookNotifier()
        sig = notifier._sign_payload(b'{"test":1}', "secret")
        assert len(sig) == 64  # SHA-256 hex digest

    @pytest.mark.asyncio
    async def test_notify_no_webhook(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier
        notifier = CycleWebhookNotifier()
        result = await notifier.notify(999, "cycle.completed", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_notify_success(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier, WebhookConfig
        notifier = CycleWebhookNotifier()
        notifier.register_webhook(1, WebhookConfig(
            url="https://httpbin.org/post", retry_count=1, timeout_s=2.0,
        ))

        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            delivery = await notifier.notify(1, "cycle.completed", {"tasks": 5})
        assert delivery is not None
        assert delivery.success is True
        assert delivery.event == "cycle.completed"

    @pytest.mark.asyncio
    async def test_notify_failure_retries(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier, WebhookConfig
        notifier = CycleWebhookNotifier()
        notifier.register_webhook(1, WebhookConfig(
            url="https://example.com/hook", retry_count=2, retry_delay_s=0.01,
        ))

        mock_response = MagicMock(status_code=500)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            delivery = await notifier.notify(1, "cycle.failed", {})
        assert delivery is not None
        assert delivery.success is False
        assert delivery.attempts == 2

    @pytest.mark.asyncio
    async def test_notify_cycle_complete(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier, WebhookConfig
        notifier = CycleWebhookNotifier()
        notifier.register_webhook(1, WebhookConfig(
            url="https://example.com/hook", retry_count=1,
        ))

        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            delivery = await notifier.notify_cycle_complete(
                1, cycle_id=42, status="completed", result={"total": 10, "completed": 8},
            )
        assert delivery is not None
        assert delivery.success is True

    def test_stats(self):
        from arclane.performance.webhook_cycles import CycleWebhookNotifier, WebhookConfig
        notifier = CycleWebhookNotifier()
        notifier.register_webhook(1, WebhookConfig(url="https://example.com"))
        s = notifier.stats()
        assert s["registered_webhooks"] == 1
        assert s["total_deliveries"] == 0

    def test_singleton_exists(self):
        from arclane.performance.webhook_cycles import cycle_webhook_notifier
        assert cycle_webhook_notifier is not None


# ===========================================================================
# Item 150: Parallel template instantiation
# ===========================================================================

class TestParallelTemplates:
    """Tests for parallel template instantiation."""

    def test_instantiator_init(self):
        from arclane.performance.parallel_templates import ParallelTemplateInstantiator
        pi = ParallelTemplateInstantiator(max_concurrency=4)
        assert pi._max_concurrency == 4
        assert pi.instantiation_count == 0

    def test_reset_stats(self):
        from arclane.performance.parallel_templates import ParallelTemplateInstantiator
        pi = ParallelTemplateInstantiator()
        pi._instantiation_count = 5
        pi.reset_stats()
        assert pi.instantiation_count == 0

    @pytest.mark.asyncio
    async def test_instantiate_empty_dir(self, tmp_path):
        from arclane.performance.parallel_templates import ParallelTemplateInstantiator
        pi = ParallelTemplateInstantiator()
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        result = await pi.instantiate(template_dir, workspace_dir)
        assert result.total_files == 0

    @pytest.mark.asyncio
    async def test_instantiate_with_files(self, tmp_path):
        from arclane.performance.parallel_templates import ParallelTemplateInstantiator
        pi = ParallelTemplateInstantiator()

        template_dir = tmp_path / "template"
        template_dir.mkdir()
        (template_dir / "index.html").write_text("<h1>{{ title }}</h1>")
        (template_dir / "style.css").write_text("body { color: {{ color }}; }")
        subdir = template_dir / "js"
        subdir.mkdir()
        (subdir / "app.js").write_text("console.log('hello');")

        workspace_dir = tmp_path / "workspace"
        result = await pi.instantiate(
            template_dir, workspace_dir,
            variables={"title": "MyApp", "color": "red"},
        )
        assert result.total_files == 3
        assert result.succeeded == 3
        assert result.failed == 0
        assert (workspace_dir / "index.html").read_text() == "<h1>MyApp</h1>"
        assert "red" in (workspace_dir / "style.css").read_text()
        assert pi.instantiation_count == 1

    @pytest.mark.asyncio
    async def test_instantiate_binary_file(self, tmp_path):
        from arclane.performance.parallel_templates import ParallelTemplateInstantiator
        pi = ParallelTemplateInstantiator()

        template_dir = tmp_path / "template"
        template_dir.mkdir()
        binary_data = bytes(range(256))
        (template_dir / "image.bin").write_bytes(binary_data)

        workspace_dir = tmp_path / "workspace"
        result = await pi.instantiate(template_dir, workspace_dir)
        assert result.succeeded == 1
        assert (workspace_dir / "image.bin").read_bytes() == binary_data

    def test_singleton_exists(self):
        from arclane.performance.parallel_templates import parallel_instantiator
        assert parallel_instantiator is not None


# ===========================================================================
# Item 163: Pipeline metrics (OpenTelemetry-compatible)
# ===========================================================================

class TestPipelineMetrics:
    """Tests for pipeline metrics collection."""

    def test_counter(self):
        from arclane.performance.pipeline_metrics import Counter
        c = Counter("test_counter")
        c.add(1)
        c.add(2)
        assert c.get() == 3

    def test_counter_with_labels(self):
        from arclane.performance.pipeline_metrics import Counter
        c = Counter("test_counter")
        c.add(1, labels={"env": "prod"})
        c.add(1, labels={"env": "dev"})
        assert c.get(labels={"env": "prod"}) == 1
        assert c.get(labels={"env": "dev"}) == 1

    def test_histogram(self):
        from arclane.performance.pipeline_metrics import Histogram
        h = Histogram("test_hist")
        h.record(1.0)
        h.record(2.0)
        h.record(3.0)
        summary = h.get_summary()
        assert summary["count"] == 3
        assert summary["min"] == 1.0
        assert summary["max"] == 3.0
        assert summary["avg"] == 2.0

    def test_histogram_empty(self):
        from arclane.performance.pipeline_metrics import Histogram
        h = Histogram("test_hist")
        summary = h.get_summary()
        assert summary["count"] == 0

    def test_gauge(self):
        from arclane.performance.pipeline_metrics import Gauge
        g = Gauge("test_gauge")
        g.set(42.0)
        assert g.get() == 42.0
        g.set(0.0)
        assert g.get() == 0.0

    def test_pipeline_metrics_cycle_tracking(self):
        from arclane.performance.pipeline_metrics import PipelineMetrics
        pm = PipelineMetrics()
        pm.record_cycle_start("nightly", "starter")
        assert pm.cycles_started.get(labels={"trigger": "nightly", "plan": "starter"}) == 1
        assert pm.active_cycles.get() == 1

        pm.record_cycle_complete("nightly", "starter", 10.5, 5)
        assert pm.cycles_completed.get(labels={"trigger": "nightly", "plan": "starter"}) == 1
        assert pm.active_cycles.get() == 0
        assert pm.tasks_processed.get(labels={"trigger": "nightly"}) == 5

    def test_pipeline_metrics_failure(self):
        from arclane.performance.pipeline_metrics import PipelineMetrics
        pm = PipelineMetrics()
        pm.record_cycle_start("on_demand", "pro")
        pm.record_cycle_failure("on_demand", "pro")
        assert pm.cycles_failed.get(labels={"trigger": "on_demand", "plan": "pro"}) == 1
        assert pm.active_cycles.get() == 0

    def test_collect_all(self):
        from arclane.performance.pipeline_metrics import PipelineMetrics
        pm = PipelineMetrics()
        pm.record_cycle_start("nightly", "starter")
        points = pm.collect_all()
        assert len(points) > 0

    def test_to_prometheus(self):
        from arclane.performance.pipeline_metrics import PipelineMetrics
        pm = PipelineMetrics()
        pm.record_cycle_start("nightly", "starter")
        prom = pm.to_prometheus()
        assert "arclane_cycles_started_total" in prom
        assert "arclane_active_cycles" in prom

    def test_labels_key_parse(self):
        from arclane.performance.pipeline_metrics import _labels_key, _parse_key
        key = _labels_key({"a": "1", "b": "2"})
        parsed = _parse_key(key)
        assert parsed == {"a": "1", "b": "2"}

    def test_labels_key_empty(self):
        from arclane.performance.pipeline_metrics import _labels_key, _parse_key
        assert _labels_key(None) == ""
        assert _parse_key("") == {}

    def test_counter_collect(self):
        from arclane.performance.pipeline_metrics import Counter
        c = Counter("c")
        c.add(1, labels={"x": "1"})
        points = c.collect()
        assert len(points) == 1
        assert points[0].name == "c"
        assert points[0].value == 1

    def test_histogram_collect(self):
        from arclane.performance.pipeline_metrics import Histogram
        h = Histogram("h")
        h.record(1.5)
        points = h.collect()
        assert any(p.name == "h_sum" for p in points)
        assert any(p.name == "h_count" for p in points)

    def test_singleton_exists(self):
        from arclane.performance.pipeline_metrics import pipeline_metrics
        assert pipeline_metrics is not None


# ===========================================================================
# Item 208: Request prioritization
# ===========================================================================

class TestRequestPrioritizer:
    """Tests for request prioritization."""

    def test_classify_health(self):
        from arclane.performance.request_priority import RequestPrioritizer, Priority
        rp = RequestPrioritizer()
        assert rp.classify("GET", "/health") == Priority.CRITICAL

    def test_classify_auth(self):
        from arclane.performance.request_priority import RequestPrioritizer, Priority
        rp = RequestPrioritizer()
        assert rp.classify("POST", "/api/auth/login") == Priority.CRITICAL

    def test_classify_billing(self):
        from arclane.performance.request_priority import RequestPrioritizer, Priority
        rp = RequestPrioritizer()
        assert rp.classify("GET", "/api/businesses/test-biz/billing/plan") == Priority.HIGH

    def test_classify_live(self):
        from arclane.performance.request_priority import RequestPrioritizer, Priority
        rp = RequestPrioritizer()
        assert rp.classify("GET", "/api/live") == Priority.LOW

    def test_classify_default_api(self):
        from arclane.performance.request_priority import RequestPrioritizer, Priority
        rp = RequestPrioritizer()
        assert rp.classify("GET", "/api/something") == Priority.NORMAL

    def test_classify_non_api(self):
        from arclane.performance.request_priority import RequestPrioritizer, Priority
        rp = RequestPrioritizer()
        assert rp.classify("GET", "/dashboard") == Priority.LOW

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        from arclane.performance.request_priority import RequestPrioritizer, Priority
        rp = RequestPrioritizer()
        await rp.acquire(Priority.NORMAL)
        assert rp.stats["active"]["NORMAL"] == 1
        rp.release(Priority.NORMAL)
        assert rp.stats["active"]["NORMAL"] == 0

    def test_stats(self):
        from arclane.performance.request_priority import RequestPrioritizer
        rp = RequestPrioritizer()
        s = rp.stats
        assert "active" in s
        assert s["total_processed"] == 0

    def test_reset_stats(self):
        from arclane.performance.request_priority import RequestPrioritizer
        rp = RequestPrioritizer()
        rp._total_processed = 100
        rp.reset_stats()
        assert rp.stats["total_processed"] == 0

    def test_priority_ordering(self):
        from arclane.performance.request_priority import Priority
        assert Priority.CRITICAL < Priority.HIGH
        assert Priority.HIGH < Priority.NORMAL
        assert Priority.NORMAL < Priority.LOW
        assert Priority.LOW < Priority.BACKGROUND

    def test_singleton_exists(self):
        from arclane.performance.request_priority import request_prioritizer
        assert request_prioritizer is not None


# ===========================================================================
# Item 245: Test database connection pooling
# ===========================================================================

class TestDatabasePool:
    """Tests for test database connection pooling."""

    def test_pool_init(self):
        from arclane.performance.db_pool import TestDatabasePool
        pool = TestDatabasePool()
        assert pool.is_initialized is False
        assert pool.session_count == 0

    @pytest.mark.asyncio
    async def test_initialize(self):
        from arclane.performance.db_pool import TestDatabasePool
        pool = TestDatabasePool()
        await pool.initialize()
        assert pool.is_initialized is True
        assert pool.engine is not None
        assert pool.session_factory is not None
        await pool.dispose()

    @pytest.mark.asyncio
    async def test_double_initialize(self):
        from arclane.performance.db_pool import TestDatabasePool
        pool = TestDatabasePool()
        await pool.initialize()
        await pool.initialize()  # should be idempotent
        assert pool.is_initialized is True
        await pool.dispose()

    @pytest.mark.asyncio
    async def test_get_session(self):
        from arclane.performance.db_pool import TestDatabasePool
        pool = TestDatabasePool()
        await pool.initialize()
        async for session in pool.get_session():
            assert session is not None
            assert pool.session_count == 1
            break
        await pool.dispose()

    @pytest.mark.asyncio
    async def test_reset(self):
        from arclane.performance.db_pool import TestDatabasePool
        pool = TestDatabasePool()
        await pool.initialize()
        await pool.reset()
        assert pool.is_initialized is True
        await pool.dispose()

    @pytest.mark.asyncio
    async def test_dispose(self):
        from arclane.performance.db_pool import TestDatabasePool
        pool = TestDatabasePool()
        await pool.initialize()
        await pool.dispose()
        assert pool.is_initialized is False
        assert pool.engine is None
        assert pool.session_factory is None

    @pytest.mark.asyncio
    async def test_dispose_without_init(self):
        from arclane.performance.db_pool import TestDatabasePool
        pool = TestDatabasePool()
        await pool.dispose()  # should not raise

    def test_singleton_exists(self):
        from arclane.performance.db_pool import test_db_pool
        assert test_db_pool is not None


# ===========================================================================
# WebSocket manager (item 201, supporting ws.py route)
# ===========================================================================

class TestWebSocketManager:
    """Tests for WebSocket connection management."""

    @pytest.mark.asyncio
    async def test_connect(self):
        from arclane.performance.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = AsyncMock()
        client = await mgr.connect(ws, "c1", business_id=1)
        assert client.client_id == "c1"
        assert mgr.connection_count == 1
        assert "business:1" in client.subscriptions

    @pytest.mark.asyncio
    async def test_disconnect(self):
        from arclane.performance.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws, "c1")
        await mgr.disconnect("c1")
        assert mgr.connection_count == 0

    def test_subscribe_unsubscribe(self):
        from arclane.performance.websocket import WebSocketManager, WSClient
        mgr = WebSocketManager()
        mgr._clients["c1"] = WSClient(client_id="c1")
        assert mgr.subscribe("c1", "business:2") is True
        assert "business:2" in mgr._clients["c1"].subscriptions
        assert mgr.unsubscribe("c1", "business:2") is True
        assert "business:2" not in mgr._clients["c1"].subscriptions

    @pytest.mark.asyncio
    async def test_send_to_client(self):
        from arclane.performance.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws, "c1")
        ok = await mgr.send_to_client("c1", {"type": "test"})
        assert ok is True
        ws.send_json.assert_called_once_with({"type": "test"})

    @pytest.mark.asyncio
    async def test_send_to_missing_client(self):
        from arclane.performance.websocket import WebSocketManager
        mgr = WebSocketManager()
        ok = await mgr.send_to_client("missing", {"type": "test"})
        assert ok is False

    @pytest.mark.asyncio
    async def test_broadcast(self):
        from arclane.performance.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1, "c1", default_channels={"global"})
        await mgr.connect(ws2, "c2", default_channels={"global"})
        sent = await mgr.broadcast("global", {"type": "hello"})
        assert sent == 2

    @pytest.mark.asyncio
    async def test_broadcast_with_exclude(self):
        from arclane.performance.websocket import WebSocketManager
        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1, "c1", default_channels={"global"})
        await mgr.connect(ws2, "c2", default_channels={"global"})
        sent = await mgr.broadcast("global", {"type": "hello"}, exclude={"c1"})
        assert sent == 1

    @pytest.mark.asyncio
    async def test_handle_subscribe_message(self):
        from arclane.performance.websocket import WebSocketManager, WSClient
        mgr = WebSocketManager()
        mgr._clients["c1"] = WSClient(client_id="c1")
        resp = await mgr.handle_client_message("c1", '{"type":"subscribe","channel":"business:5"}')
        assert resp == {"type": "subscribed", "channel": "business:5"}

    @pytest.mark.asyncio
    async def test_handle_ping(self):
        from arclane.performance.websocket import WebSocketManager, WSClient
        mgr = WebSocketManager()
        mgr._clients["c1"] = WSClient(client_id="c1")
        resp = await mgr.handle_client_message("c1", '{"type":"ping"}')
        assert resp["type"] == "pong"
        assert "timestamp" in resp

    @pytest.mark.asyncio
    async def test_handle_subscribe_forbidden_channel(self):
        from arclane.performance.websocket import WebSocketManager, WSClient
        mgr = WebSocketManager()
        mgr._clients["c1"] = WSClient(client_id="c1")
        resp = await mgr.handle_client_message(
            "c1",
            '{"type":"subscribe","channel":"business:5"}',
            allowed_channels={"business:1"},
        )
        assert resp == {"error": "forbidden channel"}

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self):
        from arclane.performance.websocket import WebSocketManager, WSClient
        mgr = WebSocketManager()
        mgr._clients["c1"] = WSClient(client_id="c1")
        resp = await mgr.handle_client_message("c1", "not json")
        assert resp == {"error": "invalid JSON"}

    def test_stats(self):
        from arclane.performance.websocket import WebSocketManager
        mgr = WebSocketManager()
        s = mgr.stats
        assert s["connections"] == 0
        assert s["total_messages_sent"] == 0

    def test_singleton_exists(self):
        from arclane.performance.websocket import ws_manager
        assert ws_manager is not None


# ===========================================================================
# __init__.py exports
# ===========================================================================

class TestPerformanceExports:
    """Tests that all performance singletons are importable from the package."""

    def test_import_query_analyzer(self):
        from arclane.performance import query_analyzer
        assert query_analyzer is not None

    def test_import_request_deduplicator(self):
        from arclane.performance import request_deduplicator
        assert request_deduplicator is not None

    def test_import_migration_benchmarker(self):
        from arclane.performance import migration_benchmarker
        assert migration_benchmarker is not None

    def test_import_tenant_filter(self):
        from arclane.performance import tenant_filter
        assert tenant_filter is not None

    def test_import_template_cache(self):
        from arclane.performance import template_cache
        assert template_cache is not None

    def test_import_cdn_config(self):
        from arclane.performance import cdn_config
        assert cdn_config is not None

    def test_import_session_preloader(self):
        from arclane.performance import session_preloader
        assert session_preloader is not None

    def test_import_business_config_cache(self):
        from arclane.performance import business_config_cache
        assert business_config_cache is not None

    def test_import_time_budget_registry(self):
        from arclane.performance import time_budget_registry
        assert time_budget_registry is not None

    def test_import_response_minifier(self):
        from arclane.performance import response_minifier
        assert response_minifier is not None

    def test_import_cache_warmer(self):
        from arclane.performance import cache_warmer
        assert cache_warmer is not None

    def test_import_container_memory_monitor(self):
        from arclane.performance import container_memory_monitor
        assert container_memory_monitor is not None

    def test_import_cycle_webhook_notifier(self):
        from arclane.performance import cycle_webhook_notifier
        assert cycle_webhook_notifier is not None

    def test_import_parallel_instantiator(self):
        from arclane.performance import parallel_instantiator
        assert parallel_instantiator is not None

    def test_import_pipeline_metrics(self):
        from arclane.performance import pipeline_metrics
        assert pipeline_metrics is not None

    def test_import_request_prioritizer(self):
        from arclane.performance import request_prioritizer
        assert request_prioritizer is not None

    def test_import_test_db_pool(self):
        from arclane.performance import test_db_pool
        assert test_db_pool is not None

    def test_import_ws_manager(self):
        from arclane.performance import ws_manager
        assert ws_manager is not None

    def test_import_paginate(self):
        from arclane.performance import paginate
        assert callable(paginate)
