"""Arclane configuration."""

import logging
import secrets

from pydantic_settings import BaseSettings

_log = logging.getLogger("arclane.config")


class ArclaneSettings(BaseSettings):
    model_config = {"env_prefix": "ARCLANE_"}

    # Database
    database_url: str = "sqlite+aiosqlite:///arclane.db"
    db_pool_size: int = 10
    db_max_overflow: int = 20
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

    # Meta Ads (Facebook / Instagram)
    meta_ads_access_token: str = ""  # System user or page access token
    meta_ads_account_id: str = ""  # act_XXXXXXXXX
    meta_ads_page_id: str = ""  # Facebook Page ID for ad creatives

    # Google Ads
    google_ads_developer_token: str = ""  # From Google Ads API Center
    google_ads_customer_id: str = ""  # xxx-xxx-xxxx (dashes stripped automatically)
    google_ads_refresh_token: str = ""  # OAuth2 refresh token for Ads API

    # LinkedIn Ads
    linkedin_ads_access_token: str = ""  # OAuth2 token with rw_ads scope
    linkedin_ads_account_id: str = ""  # Sponsored account ID (numeric)

    # Twitter/X Ads
    twitter_ads_account_id: str = ""  # Ads account ID
    twitter_ads_consumer_key: str = ""  # API key
    twitter_ads_consumer_secret: str = ""  # API secret
    twitter_ads_access_token: str = ""  # OAuth1.0a access token
    twitter_ads_access_secret: str = ""  # OAuth1.0a access token secret

    # Stripe
    stripe_enabled: bool = False
    stripe_secret_key: str = ""  # sk_test_xxx or sk_live_xxx
    stripe_webhook_secret: str = ""  # whsec_xxx for Connect webhooks
    # Direct Stripe webhook — safety net that receives raw Stripe events
    # alongside Vinzy's provisioning pipeline. If Vinzy processes first,
    # idempotency skips the duplicate. If Vinzy fails, this catches it.
    stripe_direct_webhook_secret: str = ""  # whsec_xxx from Dashboard

    # Internal orchestration
    orchestration_mode: str = "internal"  # internal | bridge
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_s: int = 60
    # Per-area model overrides — JSON mapping area name to model ID.
    # Areas not listed fall back to llm_model.
    # Example: {"strategy":"claude-sonnet-4-20250514","social":"claude-haiku-4-5-20251001"}
    llm_model_map: str = ""
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

    # Telegram (optional)
    telegram_bot_token: str = ""

    # Scheduler
    nightly_hour: int = 2  # 2 AM
    nightly_minute: int = 0

    # Limits
    max_daily_tasks: int = 1
    monthly_working_days: int = 5
    first_month_bonus: int = 10


settings = ArclaneSettings()

# Generate a random secret key if none provided (dev only)
if not settings.secret_key:
    if settings.env == "production":
        raise RuntimeError("ARCLANE_SECRET_KEY must be set in production")
    settings.secret_key = secrets.token_hex(32)
    _log.warning("No ARCLANE_SECRET_KEY set — using random key (sessions won't persist across restarts)")


def validate_production_settings() -> list[str]:
    """Validate that critical settings are configured for production.

    Returns a list of fatal error messages. Called during app startup so
    misconfigurations are caught before the server accepts traffic.
    """
    errors: list[str] = []

    if settings.env != "production":
        return errors

    # Database must be PostgreSQL in production
    if "sqlite" in settings.database_url:
        errors.append(
            "ARCLANE_DATABASE_URL uses SQLite which is not suitable for production. "
            "Set a PostgreSQL connection string."
        )

    # Webhook signing secret should be set for payload verification
    if not settings.webhook_signing_secret:
        _log.warning(
            "ARCLANE_WEBHOOK_SIGNING_SECRET is empty — webhook HMAC verification is disabled. "
            "Set this to enable payload signature checks."
        )

    # Stripe keys required when billing is enabled
    if settings.stripe_enabled:
        if not settings.stripe_secret_key:
            errors.append("ARCLANE_STRIPE_SECRET_KEY must be set when Stripe is enabled")
        if not settings.stripe_webhook_secret:
            errors.append("ARCLANE_STRIPE_WEBHOOK_SECRET must be set when Stripe is enabled")

    # Zuultimate service token required for webhook authentication
    if not settings.zuul_service_token:
        errors.append(
            "ARCLANE_ZUUL_SERVICE_TOKEN must be set in production for webhook authentication"
        )

    return errors
