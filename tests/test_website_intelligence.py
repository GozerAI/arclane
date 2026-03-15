"""Tests for website intake and summarization helpers."""

import socket

import pytest

from arclane.engine.website_intelligence import (
    _validate_fetch_destination,
    WebsiteSnapshot,
    compose_business_context,
    fetch_website_snapshot,
    normalize_website_url,
    summarize_website,
)


def test_normalize_website_url_adds_scheme():
    assert normalize_website_url("example.com") == "https://example.com"


def test_normalize_website_url_rejects_unsupported_scheme():
    with pytest.raises(ValueError):
        normalize_website_url("ftp://example.com")


def test_normalize_website_url_rejects_non_standard_port():
    with pytest.raises(ValueError):
        normalize_website_url("https://example.com:2019")


def test_summarize_website_uses_title_headings_and_excerpt():
    summary = summarize_website(
        WebsiteSnapshot(
            requested_url="https://example.com",
            final_url="https://example.com",
            title="Example Co",
            meta_description="We help operators move faster.",
            headings=["Fractional operations", "Embedded strategy"],
            excerpt="We work with B2B founders to tighten positioning and speed execution.",
        )
    )
    assert "Example Co" in summary
    assert "Fractional operations" in summary
    assert "speed execution" in summary


def test_compose_business_context_combines_description_and_site_summary():
    context = compose_business_context(
        "A service business for HVAC operators.",
        "Title: HVAC Growth Partners. Key headings: More booked jobs.",
        "https://example.com",
    )
    assert "HVAC operators" in context
    assert "HVAC Growth Partners" in context


@pytest.mark.asyncio
async def test_validate_fetch_destination_rejects_loopback():
    with pytest.raises(ValueError):
        await _validate_fetch_destination("http://127.0.0.1")


@pytest.mark.asyncio
async def test_validate_fetch_destination_rejects_private_dns_resolution(monkeypatch):
    class FakeLoop:
        async def getaddrinfo(self, host, port, type):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.12", port))]

    monkeypatch.setattr(
        "arclane.engine.website_intelligence.asyncio.get_running_loop",
        lambda: FakeLoop(),
    )

    with pytest.raises(ValueError):
        await _validate_fetch_destination("https://internal.example.com")


@pytest.mark.asyncio
async def test_fetch_website_snapshot_rejects_local_targets():
    assert await fetch_website_snapshot("http://localhost") is None
