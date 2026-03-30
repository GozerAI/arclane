"""Performance infrastructure -- caching, deduplication, budgets, and monitoring.

Provides singletons for query analysis, deduplication, caching, pagination,
time budgets, minification, container management, pipeline metrics, and more.
"""

from arclane.performance.business_cache import BusinessConfigCache, business_config_cache
from arclane.performance.cache_warming import CacheWarmer, cache_warmer
from arclane.performance.cdn_headers import CDNCacheConfig, CDNCacheMiddleware, cdn_config
from arclane.performance.container_build import (
    AsyncContainerBuilder,
    ContainerMemoryMonitor,
    MemoryConfig,
    async_container_builder,
    container_memory_monitor,
)
from arclane.performance.db_pool import TestDatabasePool, test_db_pool
from arclane.performance.deduplication import RequestDeduplicator, request_deduplicator
from arclane.performance.migration_benchmark import MigrationBenchmarker, migration_benchmarker
from arclane.performance.minification import MinificationMiddleware, ResponseMinifier, response_minifier
from arclane.performance.pagination import PaginatedResponse, PaginationParams, paginate
from arclane.performance.parallel_templates import ParallelTemplateInstantiator, parallel_instantiator
from arclane.performance.pipeline_metrics import PipelineMetrics, pipeline_metrics
from arclane.performance.query_analysis import QueryAnalyzer, query_analyzer
from arclane.performance.request_priority import RequestPrioritizer, request_prioritizer
from arclane.performance.row_level_security import TenantContext, TenantFilter, tenant_filter
from arclane.performance.session_preload import SessionPreloader, session_preloader
from arclane.performance.template_cache import TemplateRenderCache, template_cache
from arclane.performance.time_budgets import TimeBudgetMiddleware, TimeBudgetRegistry, time_budget_registry
from arclane.performance.webhook_cycles import CycleWebhookNotifier, cycle_webhook_notifier
from arclane.performance.websocket import WebSocketManager, ws_manager

__all__ = [
    # Query analysis (item 6)
    "QueryAnalyzer", "query_analyzer",
    # Deduplication (item 15)
    "RequestDeduplicator", "request_deduplicator",
    # Migration benchmark (item 23)
    "MigrationBenchmarker", "migration_benchmarker",
    # Row-level security (item 30)
    "TenantFilter", "TenantContext", "tenant_filter",
    # Template cache (item 41)
    "TemplateRenderCache", "template_cache",
    # CDN headers (items 49, 215)
    "CDNCacheConfig", "CDNCacheMiddleware", "cdn_config",
    # Session preload (item 57)
    "SessionPreloader", "session_preloader",
    # Business config cache (item 65)
    "BusinessConfigCache", "business_config_cache",
    # Pagination (item 75)
    "PaginationParams", "PaginatedResponse", "paginate",
    # Time budgets (item 83)
    "TimeBudgetRegistry", "TimeBudgetMiddleware", "time_budget_registry",
    # Minification (item 89)
    "ResponseMinifier", "MinificationMiddleware", "response_minifier",
    # Cache warming (item 96)
    "CacheWarmer", "cache_warmer",
    # Container build (items 136, 175, 186)
    "AsyncContainerBuilder", "async_container_builder",
    "ContainerMemoryMonitor", "container_memory_monitor", "MemoryConfig",
    # Webhook cycles (item 142)
    "CycleWebhookNotifier", "cycle_webhook_notifier",
    # Parallel templates (item 150)
    "ParallelTemplateInstantiator", "parallel_instantiator",
    # Pipeline metrics (item 163)
    "PipelineMetrics", "pipeline_metrics",
    # Request priority (item 208)
    "RequestPrioritizer", "request_prioritizer",
    # DB pool (item 245)
    "TestDatabasePool", "test_db_pool",
    # WebSocket (item 201)
    "WebSocketManager", "ws_manager",
]
