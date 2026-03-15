"""Arclane configuration."""

import logging
import secrets

from pydantic_settings import BaseSettings

_log = logging.getLogger("arclane.config")


class ArclaneSettings(BaseSettings):
    model_config = {"env_prefix": "ARCLANE_"}

    # Database
    database_url: str = "sqlite+aiosqlite:///arclane.db"
    secret_key: str = ""

    # Environment
    env: str = "development"  # development | production
    sentry_dsn: str = ""

    # Domain
    domain: str = "arclane.cloud"
    caddy_admin_url: str = "http://localhost:2019"

    # Upstream services
    csuite_base_url: str = "http://localhost:8007"
    zuultimate_base_url: str = "http://localhost:8000"
    vinzy_base_url: str = "http://localhost:8001"
    trendscope_base_url: str = "http://localhost:8002"
    kh_base_url: str = "http://localhost:8011"
    nexus_base_url: str = "http://localhost:8008"
    zuul_service_token: str = ""
    webhook_signing_secret: str = ""

    # Stripe (via Vinzy provisioning)
    stripe_enabled: bool = False

    # Internal orchestration
    orchestration_mode: str = "internal"  # internal | bridge
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_s: int = 60
    website_fetch_timeout_s: int = 8
    workspaces_root: str = "/var/arclane/workspaces"

    # OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""

    # Public live feed
    public_live_feed_identity: bool = False
    public_live_feed_detail: bool = False

    # Email
    resend_api_key: str = ""
    email_from_domain: str = "arclane.cloud"

    # Scheduler
    nightly_hour: int = 2  # 2 AM
    nightly_minute: int = 0

    # Limits
    max_daily_tasks: int = 1
    monthly_credits: int = 5
    first_month_bonus: int = 10


settings = ArclaneSettings()

# Generate a random secret key if none provided (dev only)
if not settings.secret_key:
    if settings.env == "production":
        raise RuntimeError("ARCLANE_SECRET_KEY must be set in production")
    settings.secret_key = secrets.token_hex(32)
    _log.warning("No ARCLANE_SECRET_KEY set — using random key (sessions won't persist across restarts)")
