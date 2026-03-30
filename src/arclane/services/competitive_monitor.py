"""Competitive monitoring — track competitors and generate intelligence briefs."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import Business, CompetitiveMonitor

log = get_logger("competitive_monitor")


async def add_competitor(
    business: Business,
    session: AsyncSession,
    *,
    name: str,
    url: str | None = None,
) -> CompetitiveMonitor:
    """Add a competitor to monitor."""
    # Check for existing
    result = await session.execute(
        select(CompetitiveMonitor).where(
            CompetitiveMonitor.business_id == business.id,
            CompetitiveMonitor.competitor_name == name,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.competitor_url = url or existing.competitor_url
        await session.flush()
        return existing

    monitor = CompetitiveMonitor(
        business_id=business.id,
        competitor_name=name,
        competitor_url=url,
    )
    session.add(monitor)
    await session.flush()
    log.info("Competitor '%s' added for %s", name, business.slug)
    return monitor


async def get_competitors(business: Business, session: AsyncSession) -> list[dict]:
    """Return all monitored competitors for a business."""
    result = await session.execute(
        select(CompetitiveMonitor)
        .where(CompetitiveMonitor.business_id == business.id)
        .order_by(CompetitiveMonitor.created_at)
    )
    monitors = result.scalars().all()
    return [
        {
            "id": m.id,
            "name": m.competitor_name,
            "url": m.competitor_url,
            "findings": m.findings_json,
            "last_checked_at": m.last_checked_at.isoformat() if m.last_checked_at else None,
            "created_at": m.created_at.isoformat(),
        }
        for m in monitors
    ]


async def run_check(business: Business, session: AsyncSession, competitor_id: int | None = None) -> list[dict]:
    """Run a competitive check for one or all monitored competitors."""
    query = select(CompetitiveMonitor).where(CompetitiveMonitor.business_id == business.id)
    if competitor_id:
        query = query.where(CompetitiveMonitor.id == competitor_id)

    result = await session.execute(query)
    monitors = result.scalars().all()

    results = []
    for monitor in monitors:
        try:
            findings = await _check_competitor(monitor)
            monitor.findings_json = findings
            monitor.last_checked_at = datetime.now(timezone.utc)
            results.append({
                "competitor": monitor.competitor_name,
                "status": "checked",
                "findings": findings,
            })
        except Exception as exc:
            log.warning("Check failed for competitor '%s': %s", monitor.competitor_name, exc)
            results.append({
                "competitor": monitor.competitor_name,
                "status": "failed",
                "error": str(exc),
            })

    await session.flush()
    return results


async def get_competitive_brief(business: Business, session: AsyncSession) -> dict:
    """Generate a competitive intelligence brief from all monitored competitors."""
    competitors = await get_competitors(business, session)

    brief = {
        "business": business.name,
        "competitors_tracked": len(competitors),
        "competitors": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    for comp in competitors:
        findings = comp.get("findings") or {}
        brief["competitors"].append({
            "name": comp["name"],
            "url": comp.get("url"),
            "last_checked": comp.get("last_checked_at"),
            "summary": findings.get("summary", "No analysis available yet."),
            "strengths": findings.get("strengths", []),
            "weaknesses": findings.get("weaknesses", []),
            "opportunities": findings.get("opportunities", []),
        })

    return brief


async def generate_competitive_advisories(
    business: Business,
    check_results: list[dict],
    session: AsyncSession,
) -> list[dict]:
    """Generate advisory notes from competitive check results."""
    from arclane.models.tables import AdvisoryNote

    notes = []
    for result in check_results:
        if result.get("status") != "checked":
            continue
        findings = result.get("findings", {})

        # Messaging change detected
        if findings.get("messaging_changed"):
            notes.append({
                "category": "warning",
                "title": f"Competitor messaging changed: {result['competitor']}",
                "body": f"{result['competitor']} updated their website messaging. Review your positioning to maintain differentiation.",
                "priority": 7,
            })

        # Market signals
        signals = findings.get("market_signals", [])
        strong_signals = [s for s in signals if s.get("score", 0) >= 70]
        if strong_signals:
            signal_names = ", ".join(s.get("name", "?") for s in strong_signals[:3])
            notes.append({
                "category": "insight",
                "title": f"Strong market signals near {result['competitor']}",
                "body": f"Trending signals: {signal_names}. Consider how to position against these trends.",
                "priority": 5,
            })

    # Persist notes
    for note in notes:
        session.add(AdvisoryNote(
            business_id=business.id,
            category=note["category"],
            title=note["title"],
            body=note["body"],
            priority=note["priority"],
        ))

    if notes:
        await session.flush()
        log.info("Generated %d competitive advisories for %s", len(notes), business.slug)

    return notes


async def _check_competitor(monitor: CompetitiveMonitor) -> dict:
    """Gather competitive intelligence from available data sources.

    Uses TrendscopeClient for market signals and website_intelligence
    for competitor website analysis. Graceful degradation if sources are unavailable.
    """
    findings: dict = {
        "summary": "",
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "messaging_snapshot": None,
        "market_signals": [],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Fetch competitor website snapshot if URL is available
    if monitor.competitor_url:
        try:
            from arclane.engine.website_intelligence import (
                fetch_website_snapshot,
                summarize_website,
            )
            snapshot = await fetch_website_snapshot(monitor.competitor_url)
            summary = summarize_website(snapshot)
            findings["messaging_snapshot"] = summary

            # Extract competitive signals from the snapshot
            if summary:
                findings["summary"] = (
                    f"Website analysis for {monitor.competitor_name}: {summary[:300]}"
                )
        except Exception as exc:
            log.warning("Website fetch failed for %s: %s", monitor.competitor_name, exc)
            findings["summary"] = f"Could not fetch website for {monitor.competitor_name}."

    # 2. Fetch relevant market signals from Trendscope
    try:
        from arclane.integrations.trendscope_client import TrendscopeClient
        ts = TrendscopeClient()
        signals = await ts.get_relevant_signals(
            f"{monitor.competitor_name} market competition", limit=3,
        )
        if signals:
            findings["market_signals"] = signals
    except Exception as exc:
        log.warning("Trendscope signals failed for %s: %s", monitor.competitor_name, exc)

    # 3. Diff against previous findings to detect changes
    previous = monitor.findings_json or {}
    prev_snapshot = previous.get("messaging_snapshot", "")
    curr_snapshot = findings.get("messaging_snapshot", "")

    if prev_snapshot and curr_snapshot and prev_snapshot != curr_snapshot:
        findings["messaging_changed"] = True
        findings["opportunities"].append(
            f"Messaging change detected for {monitor.competitor_name} — review their new positioning."
        )

    if not findings["summary"]:
        findings["summary"] = f"Competitive check for {monitor.competitor_name} completed."

    return findings
