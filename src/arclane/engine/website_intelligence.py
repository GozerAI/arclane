"""Utilities for reading and summarizing an existing business website."""

import asyncio
from dataclasses import dataclass, field
from html.parser import HTMLParser
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("website_intelligence")

ALLOWED_WEBSITE_SCHEMES = {"http", "https"}
ALLOWED_WEBSITE_PORTS = {None, 80, 443}
MAX_FETCH_REDIRECTS = 3


def _clean_text(value: str) -> str:
    return " ".join((value or "").split())


def normalize_website_url(url: str) -> str:
    """Normalize user input into a fetchable URL."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ALLOWED_WEBSITE_SCHEMES or not parsed.netloc:
        raise ValueError("Invalid website URL")
    if parsed.username or parsed.password:
        raise ValueError("Invalid website URL")
    if parsed.port not in ALLOWED_WEBSITE_PORTS:
        raise ValueError("Unsupported website port")
    return parsed._replace(fragment="").geturl()


def _is_public_ip(address: str) -> bool:
    return ipaddress.ip_address(address).is_global


async def _validate_fetch_destination(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().rstrip(".").lower()
    if not hostname:
        raise ValueError("Invalid website URL")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local destinations are not allowed")

    try:
        if not _is_public_ip(hostname):
            raise ValueError("Private destinations are not allowed")
        return
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise

    try:
        loop = asyncio.get_running_loop()
        resolved = await loop.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("Unable to resolve website host") from exc

    addresses = {item[4][0] for item in resolved if item[4]}
    if not addresses:
        raise ValueError("Unable to resolve website host")
    if any(not _is_public_ip(address) for address in addresses):
        raise ValueError("Private destinations are not allowed")


class _WebsiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_chunks: list[str] = []
        self.body_chunks: list[str] = []
        self.headings: list[str] = []
        self.meta_description: str | None = None
        self._skip_depth = 0
        self._in_title = False
        self._heading_chunks: list[str] | None = None

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in {"h1", "h2", "h3"}:
            self._heading_chunks = []
            return
        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            if name == "description":
                content = _clean_text(attrs_dict.get("content", ""))
                if content:
                    self.meta_description = content

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in {"h1", "h2", "h3"} and self._heading_chunks is not None:
            heading = _clean_text("".join(self._heading_chunks))
            if heading:
                self.headings.append(heading)
            self._heading_chunks = None

    def handle_data(self, data: str):
        if self._skip_depth:
            return
        text = _clean_text(data)
        if not text:
            return
        if self._in_title:
            self.title_chunks.append(text)
            return
        if self._heading_chunks is not None:
            self._heading_chunks.append(text)
            return
        self.body_chunks.append(text)


@dataclass
class WebsiteSnapshot:
    requested_url: str
    final_url: str
    title: str | None = None
    meta_description: str | None = None
    headings: list[str] = field(default_factory=list)
    excerpt: str | None = None


async def fetch_website_snapshot(url: str) -> WebsiteSnapshot | None:
    """Fetch the site homepage and extract enough context for optimization work."""
    try:
        normalized_url = normalize_website_url(url)
        await _validate_fetch_destination(normalized_url)
    except ValueError:
        return None

    try:
        async with httpx.AsyncClient(
            timeout=settings.website_fetch_timeout_s,
            follow_redirects=False,
            headers={"User-Agent": "Arclane/1.0 (+https://arclane.cloud)"},
        ) as client:
            next_url = normalized_url
            response = None
            for _ in range(MAX_FETCH_REDIRECTS + 1):
                response = await client.get(next_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    next_url = normalize_website_url(str(response.url.join(location)))
                    await _validate_fetch_destination(next_url)
                    continue
                response.raise_for_status()
                break
            else:
                log.warning("Website fetch aborted for %s after too many redirects", normalized_url)
                return None
    except Exception:
        log.warning("Website fetch failed for %s", normalized_url, exc_info=True)
        return None

    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        log.warning("Website fetch skipped for %s because content type was %s", normalized_url, content_type)
        return None

    parser = _WebsiteParser()
    parser.feed(response.text[:200000])

    excerpt = _clean_text(" ".join(parser.body_chunks[:80]))
    return WebsiteSnapshot(
        requested_url=normalized_url,
        final_url=str(response.url),
        title=_clean_text(" ".join(parser.title_chunks)) or None,
        meta_description=parser.meta_description,
        headings=parser.headings[:6],
        excerpt=excerpt or None,
    )


def summarize_website(snapshot: WebsiteSnapshot | None) -> str | None:
    """Create a short site summary for task planning and execution."""
    if not snapshot:
        return None

    parts: list[str] = []
    if snapshot.title:
        parts.append(f"Title: {snapshot.title}.")
    if snapshot.meta_description:
        parts.append(f"Meta description: {snapshot.meta_description}.")
    if snapshot.headings:
        parts.append(f"Key headings: {'; '.join(snapshot.headings[:4])}.")
    if snapshot.excerpt:
        parts.append(f"On-page copy sample: {snapshot.excerpt[:450]}")

    summary = " ".join(parts).strip()
    return summary[:1200] or None


def compose_business_context(description: str | None, website_summary: str | None, website_url: str | None = None) -> str:
    """Combine user instructions and website context into a durable business brief."""
    parts: list[str] = []
    if description:
        parts.append(description.strip())
    if website_summary:
        parts.append(f"Existing website baseline: {website_summary}")
    elif website_url:
        parts.append(f"Optimize the existing business at {website_url}.")
    return "\n\n".join(part for part in parts if part).strip()
