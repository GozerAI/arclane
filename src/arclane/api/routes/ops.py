"""Operational routes — Prometheus metrics, webhook management, time budget stats."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.performance.pipeline_metrics import pipeline_metrics
from arclane.performance.time_budgets import time_budget_registry
from arclane.performance.webhook_cycles import (
    CycleWebhookNotifier,
    WebhookConfig,
    cycle_webhook_notifier,
)
from arclane.performance.websocket import ws_manager
from arclane.performance.business_cache import business_config_cache
from arclane.performance.cache_warming import cache_warmer
from arclane.performance.deduplication import request_deduplicator
from arclane.performance.minification import response_minifier
from arclane.performance.request_priority import request_prioritizer
from arclane.performance.row_level_security import tenant_filter
from arclane.performance.template_cache import template_cache
from arclane.performance.container_build import container_memory_monitor
from arclane.models.tables import Business
from arclane.services.content_publisher import content_publisher

router = APIRouter()


# --- Prometheus metrics ---


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Export pipeline metrics in Prometheus text format."""
    return pipeline_metrics.to_prometheus()


# --- Time budget stats ---


@router.get("/time-budgets")
async def time_budget_stats():
    """Get time budget violation statistics."""
    return time_budget_registry.stats()


# --- WebSocket stats ---


@router.get("/ws-stats")
async def websocket_stats():
    """Get WebSocket connection statistics."""
    return ws_manager.stats


# --- Webhook management ---


class WebhookRegisterRequest(BaseModel):
    url: str
    secret: str = ""
    timeout_s: float = 15.0
    retry_count: int = 3


class WebhookResponse(BaseModel):
    business_id: int
    url: str
    registered: bool


@router.post("/webhooks", response_model=WebhookResponse)
async def register_webhook(
    payload: WebhookRegisterRequest,
    business: Business = Depends(get_business),
):
    """Register a webhook URL to receive cycle completion notifications."""
    config = WebhookConfig(
        url=payload.url,
        secret=payload.secret,
        timeout_s=payload.timeout_s,
        retry_count=payload.retry_count,
    )
    cycle_webhook_notifier.register_webhook(business.id, config)
    return WebhookResponse(
        business_id=business.id, url=payload.url, registered=True,
    )


@router.delete("/webhooks")
async def unregister_webhook(
    business: Business = Depends(get_business),
):
    """Remove webhook registration for a business."""
    cycle_webhook_notifier.unregister_webhook(business.id)
    return {"status": "ok", "business_id": business.id}


@router.get("/webhooks")
async def get_webhook(
    business: Business = Depends(get_business),
):
    """Get current webhook config for a business."""
    config = cycle_webhook_notifier.get_webhook(business.id)
    if not config:
        return None
    return {"url": config.url, "timeout_s": config.timeout_s, "retry_count": config.retry_count}


@router.get("/webhooks/stats")
async def webhook_stats():
    """Get webhook delivery statistics."""
    return cycle_webhook_notifier.stats()


# --- Content publishing stats ---


@router.get("/publishing/stats")
async def publishing_stats():
    """Get content publishing statistics."""
    return content_publisher.stats()


# --- Deduplication stats ---


@router.get("/dedup/stats")
async def dedup_stats():
    """Get request deduplication statistics."""
    return request_deduplicator.stats


# --- Row-level security stats ---


@router.get("/rls/stats")
async def rls_stats():
    """Get row-level security filter statistics."""
    return {
        "enabled": tenant_filter.enabled,
        "filter_count": tenant_filter.filter_count,
    }


# --- Business config cache stats ---


@router.get("/cache/stats")
async def cache_stats():
    """Get business config cache statistics."""
    return business_config_cache.stats


# --- Cache warming stats ---


@router.get("/warming/stats")
async def warming_stats():
    """Get cache warming statistics."""
    return cache_warmer.stats


# --- Minification stats ---


@router.get("/minification/stats")
async def minification_stats():
    """Get response minification statistics."""
    return {
        "enabled": response_minifier.enabled,
        "bytes_saved": response_minifier.bytes_saved,
    }


# --- Request priority stats ---


@router.get("/priority/stats")
async def priority_stats():
    """Get request priority concurrency statistics."""
    return request_prioritizer.stats


# --- Template cache stats ---


@router.get("/template-cache/stats")
async def template_cache_stats():
    """Get template render cache statistics."""
    return template_cache.stats


# --- Container memory monitor stats ---


@router.get("/containers/memory")
async def container_memory_stats():
    """Get container memory monitor statistics."""
    return container_memory_monitor.stats()


# --- Combined system status ---


@router.get("/status")
async def system_status():
    """Aggregated system status across all performance subsystems."""
    return {
        "pipeline_metrics": {
            "active_cycles": pipeline_metrics.active_cycles.get(),
        },
        "websocket": {
            "connections": ws_manager.connection_count,
        },
        "webhooks": cycle_webhook_notifier.stats(),
        "publishing": content_publisher.stats(),
        "cache": business_config_cache.stats,
        "minification": {
            "bytes_saved": response_minifier.bytes_saved,
        },
        "time_budgets": {
            "violations": len(time_budget_registry.violations),
        },
        "rls": {
            "filter_count": tenant_filter.filter_count,
        },
        "priority": request_prioritizer.stats,
        "dedup": request_deduplicator.stats,
    }
