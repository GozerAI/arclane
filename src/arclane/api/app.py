"""Arclane FastAPI application."""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from arclane.core.config import settings
from arclane.core.database import init_db
from arclane.core.logging import get_logger
from arclane.engine.scheduler import start_scheduler, stop_scheduler

FRONTEND_DIR = Path(__file__).parent.parent.parent.parent / "frontend"

log = get_logger("api")

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


def _init_sentry():
    """Initialize Sentry error tracking if DSN is configured."""
    dsn = getattr(settings, "sentry_dsn", "")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.env,
            traces_sample_rate=0.1,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        )
        log.info("Sentry initialized")
    except ImportError:
        log.warning("sentry-sdk not installed — error tracking disabled")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Arclane starting up")
    _init_sentry()
    await init_db()
    start_scheduler()
    log.info("Arclane ready — %s", settings.domain)
    yield
    stop_scheduler()
    log.info("Arclane shut down")


app = FastAPI(
    title="Arclane",
    description="Autonomous business engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"https://{settings.domain}",
        f"https://*.{settings.domain}",
        "http://localhost:8012",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Service-Token"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log API requests with timing in production."""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        log.info(
            "%s %s %d %.0fms",
            request.method, request.url.path,
            response.status_code, duration_ms,
        )
        return response


app.add_middleware(RequestLoggingMiddleware)

# Import and register routes
from arclane.api.routes import auth, billing, content, cycles, feed, intake, live, metrics, settings as settings_routes, workflows  # noqa: E402

app.include_router(intake.router, prefix="/api/businesses", tags=["intake"])
app.include_router(feed.router, prefix="/api/businesses/{business_slug}/feed", tags=["feed"])
app.include_router(content.router, prefix="/api/businesses/{business_slug}/content", tags=["content"])
app.include_router(metrics.router, prefix="/api/businesses/{business_slug}/metrics", tags=["metrics"])
app.include_router(
    settings_routes.router,
    prefix="/api/businesses/{business_slug}/settings",
    tags=["settings"],
)
app.include_router(
    cycles.router,
    prefix="/api/businesses/{business_slug}/cycles",
    tags=["cycles"],
)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(live.router, prefix="/api/live", tags=["live"])
app.include_router(
    billing.router,
    prefix="/api/businesses/{business_slug}/billing",
    tags=["billing"],
)
app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"])


@app.get("/health")
async def health():
    """Basic health check — always fast."""
    return {"status": "ok"}


@app.get("/health/detailed")
async def health_detailed():
    """Detailed health check — tests DB and upstream services."""
    import httpx
    from arclane.core.database import check_db_health

    checks = {"database": await check_db_health()}

    # Check external service reachability
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.external_service_url}/health")
            checks["external_service"] = resp.status_code == 200
    except Exception:
        checks["external_service"] = False

    # Check Zuultimate reachability
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.zuultimate_base_url}/health")
            checks["zuultimate"] = resp.status_code == 200
    except Exception:
        checks["zuultimate"] = False

    overall = all(checks.values())
    return {"status": "ok" if overall else "degraded", "checks": checks}


@app.get("/robots.txt")
async def robots():
    return Response(
        content="User-agent: *\nAllow: /\nAllow: /live\nDisallow: /api/\nDisallow: /dashboard\nSitemap: https://arclane.cloud/sitemap.xml\n",
        media_type="text/plain",
    )


# Serve frontend static files and SPA fallback
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/")
    async def serve_landing():
        """Public marketing landing page."""
        return FileResponse(FRONTEND_DIR / "landing.html")

    @app.get("/live")
    async def serve_live_page():
        """Public live feed page — no auth required."""
        return FileResponse(FRONTEND_DIR / "live.html")

    @app.get("/dashboard")
    async def serve_dashboard():
        """Dashboard SPA."""
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """SPA fallback — serve index.html for non-API routes."""
        return FileResponse(FRONTEND_DIR / "index.html")
