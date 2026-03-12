"""Test configuration."""

from arclane.core.config import ArclaneSettings


def test_defaults():
    s = ArclaneSettings()
    assert s.domain == "arclane.cloud"
    assert s.nightly_hour == 2
    assert s.max_daily_tasks == 1
    assert s.monthly_credits == 5
    assert s.first_month_bonus == 10
