"""Subdomain provisioning via Caddy admin API.

Caddy handles wildcard TLS certs and reverse proxying for *.arclane.cloud.
Each business gets {slug}.arclane.cloud routed to its container.
"""

import httpx

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("provisioning.subdomain")


async def provision_subdomain(slug: str, upstream_port: int | None = None) -> None:
    """Register a subdomain route in Caddy.

    If no upstream_port is provided, routes to a default holding page.
    Once the app is deployed, call update_subdomain_upstream() with the real port.
    """
    domain = f"{slug}.{settings.domain}"
    upstream = f"localhost:{upstream_port}" if upstream_port else "localhost:8099"  # holding page

    caddy_route = {
        "@id": f"route-{slug}",
        "match": [{"host": [domain]}],
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": upstream}],
            }
        ],
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.caddy_admin_url}/config/apps/http/servers/arclane/routes",
            json=caddy_route,
            timeout=10.0,
        )
        resp.raise_for_status()

    log.info("Subdomain route created: %s -> %s", domain, upstream)


async def update_subdomain_upstream(slug: str, upstream_port: int) -> None:
    """Update an existing subdomain route to point to a new upstream."""
    upstream = f"localhost:{upstream_port}"

    patch = {
        "handle": [
            {
                "handler": "reverse_proxy",
                "upstreams": [{"dial": upstream}],
            }
        ],
    }

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{settings.caddy_admin_url}/id/route-{slug}",
            json=patch,
            timeout=10.0,
        )
        resp.raise_for_status()

    log.info("Subdomain upstream updated: %s.%s -> %s", slug, settings.domain, upstream)
