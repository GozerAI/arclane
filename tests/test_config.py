"""Test configuration."""

from unittest.mock import patch

from arclane.core.config import ArclaneSettings, validate_production_settings


def test_defaults():
    s = ArclaneSettings()
    assert s.domain == "arclane.cloud"
    assert s.orchestration_mode == "internal"
    assert s.nightly_hour == 2
    assert s.max_daily_tasks == 1
    assert s.monthly_working_days == 5
    assert s.first_month_bonus == 10
    assert s.website_fetch_timeout_s == 8


def test_validate_production_skips_in_development():
    """No errors in development mode regardless of config."""
    with patch("arclane.core.config.settings") as s:
        s.env = "development"
        assert validate_production_settings() == []


def test_validate_production_rejects_sqlite():
    with patch("arclane.core.config.settings") as s:
        s.env = "production"
        s.database_url = "sqlite+aiosqlite:///arclane.db"
        s.webhook_signing_secret = "secret"
        s.stripe_enabled = False
        s.zuul_service_token = "token"
        errors = validate_production_settings()
        assert any("SQLite" in e for e in errors)


def test_validate_production_requires_stripe_keys():
    with patch("arclane.core.config.settings") as s:
        s.env = "production"
        s.database_url = "postgresql+asyncpg://user:pass@host/db"
        s.webhook_signing_secret = "secret"
        s.stripe_enabled = True
        s.stripe_secret_key = ""
        s.stripe_webhook_secret = ""
        s.zuul_service_token = "token"
        errors = validate_production_settings()
        assert any("STRIPE_SECRET_KEY" in e for e in errors)
        assert any("STRIPE_WEBHOOK_SECRET" in e for e in errors)


def test_validate_production_requires_service_token():
    with patch("arclane.core.config.settings") as s:
        s.env = "production"
        s.database_url = "postgresql+asyncpg://user:pass@host/db"
        s.webhook_signing_secret = "secret"
        s.stripe_enabled = False
        s.zuul_service_token = ""
        errors = validate_production_settings()
        assert any("SERVICE_TOKEN" in e for e in errors)


def test_validate_production_passes_with_valid_config():
    with patch("arclane.core.config.settings") as s:
        s.env = "production"
        s.database_url = "postgresql+asyncpg://user:pass@host/db"
        s.webhook_signing_secret = "secret"
        s.stripe_enabled = False
        s.zuul_service_token = "token"
        assert validate_production_settings() == []
