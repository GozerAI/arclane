"""Content calendar — rolling 30-day content planning tied to market signals."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.core.logging import get_logger
from arclane.models.tables import Business, Content

log = get_logger("content_calendar")

# Content types and their default frequencies (posts per month)
DEFAULT_CADENCE = {
    "blog": 4,       # Weekly blog post
    "social": 12,    # 3x per week
    "newsletter": 4,  # Weekly newsletter
    "report": 1,     # Monthly report
}

# Topic categories based on common business needs
TOPIC_CATEGORIES = [
    "industry_trends",
    "product_updates",
    "customer_stories",
    "how_to_guides",
    "thought_leadership",
    "behind_the_scenes",
    "competitive_analysis",
    "tips_and_tricks",
]


async def generate_calendar(
    business: Business,
    session: AsyncSession,
    days: int = 30,
) -> dict:
    """Generate a rolling content calendar for the next N days."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    # Get existing scheduled/draft content
    result = await session.execute(
        select(Content).where(
            Content.business_id == business.id,
            Content.status.in_(["draft", "scheduled"]),
        ).order_by(Content.created_at)
    )
    existing = result.scalars().all()

    # Get recent published content to avoid repetition
    recent_result = await session.execute(
        select(Content).where(
            Content.business_id == business.id,
            Content.status == "published",
        ).order_by(Content.created_at.desc()).limit(20)
    )
    recent = recent_result.scalars().all()
    recent_types = [c.content_type for c in recent]

    # Build calendar slots
    slots = []
    for day_offset in range(days):
        date = now + timedelta(days=day_offset)
        day_of_week = date.weekday()  # 0=Mon, 6=Sun

        # Skip weekends for most content
        if day_of_week >= 5:
            continue

        # Determine what content to suggest for this day
        day_slots = _slots_for_day(date, day_of_week, recent_types)
        for slot in day_slots:
            # Check if already have content for this slot
            existing_match = next(
                (c for c in existing if c.content_type == slot["content_type"]
                 and c.published_at and abs((c.published_at - date).days) < 1),
                None,
            )
            if existing_match:
                slot["status"] = "filled"
                slot["content_id"] = existing_match.id
                slot["title"] = existing_match.title
            else:
                slot["status"] = "open"
                slot["suggested_topic"] = _suggest_topic(
                    business, slot["content_type"], recent_types,
                )

            slot["date"] = date.strftime("%Y-%m-%d")
            slots.append(slot)

    return {
        "business": business.name,
        "period": {"start": now.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
        "total_slots": len(slots),
        "filled": sum(1 for s in slots if s["status"] == "filled"),
        "open": sum(1 for s in slots if s["status"] == "open"),
        "slots": slots,
    }


async def get_gaps(business: Business, session: AsyncSession) -> list[dict]:
    """Identify content gaps — areas where production is behind schedule."""
    now = datetime.now(timezone.utc)
    month_ago = now - timedelta(days=30)

    result = await session.execute(
        select(Content.content_type, func.count(Content.id))
        .where(
            Content.business_id == business.id,
            Content.created_at >= month_ago,
        )
        .group_by(Content.content_type)
    )
    actual = dict(result.all())

    gaps = []
    for content_type, target in DEFAULT_CADENCE.items():
        current = actual.get(content_type, 0)
        if current < target:
            gaps.append({
                "content_type": content_type,
                "target": target,
                "actual": current,
                "deficit": target - current,
                "priority": "high" if current == 0 else "medium",
            })

    return sorted(gaps, key=lambda x: x["deficit"], reverse=True)


def _slots_for_day(date: datetime, day_of_week: int, recent_types: list) -> list[dict]:
    """Determine content slots for a given day of the week."""
    slots = []

    # Monday: blog post
    if day_of_week == 0:
        slots.append({"content_type": "blog", "slot_type": "weekly_blog"})

    # Mon/Wed/Fri: social post
    if day_of_week in (0, 2, 4):
        slots.append({"content_type": "social", "slot_type": "social_post"})

    # Thursday: newsletter
    if day_of_week == 3:
        slots.append({"content_type": "newsletter", "slot_type": "weekly_newsletter"})

    # First Monday of month: report
    if day_of_week == 0 and date.day <= 7:
        slots.append({"content_type": "report", "slot_type": "monthly_report"})

    return slots


def _suggest_topic(business: Business, content_type: str, recent_types: list) -> str:
    """Suggest a topic based on content type and business context."""
    description = (business.description or "")[:200]

    topic_map = {
        "blog": f"Write about a key insight or lesson from building {business.name}",
        "social": f"Share a quick tip or update about {business.name}",
        "newsletter": f"Weekly update: what {business.name} accomplished and what's next",
        "report": f"Monthly performance review for {business.name}",
    }

    return topic_map.get(content_type, f"Content for {business.name}")


async def auto_fill_calendar(
    business: Business,
    session: AsyncSession,
    days_ahead: int = 7,
    max_drafts: int = 5,
) -> list[dict]:
    """Auto-generate draft content for open calendar slots.

    Creates structured drafts the founder can review and publish.
    Returns list of created content items.
    """
    calendar = await generate_calendar(business, session, days=days_ahead)
    open_slots = [s for s in calendar["slots"] if s["status"] == "open"]

    if not open_slots:
        return []

    created = []
    phase = getattr(business, "current_phase", 0) or 0
    description = (business.description or "")[:300]

    for slot in open_slots[:max_drafts]:
        content_type = slot["content_type"]
        topic = slot.get("suggested_topic", f"Content for {business.name}")
        date_str = slot.get("date", "")

        title, body = _generate_draft(business.name, description, content_type, topic, phase)

        content = Content(
            business_id=business.id,
            content_type=content_type,
            title=title,
            body=body,
            status="draft",
            published_at=datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) if date_str else None,
            metadata_json={
                "auto_generated": True,
                "calendar_slot": slot.get("slot_type"),
                "suggested_topic": topic,
                "phase": phase,
            },
        )
        session.add(content)
        created.append({
            "content_type": content_type,
            "title": title,
            "date": date_str,
            "slot_type": slot.get("slot_type"),
        })

    if created:
        await session.flush()
        log.info("Auto-filled %d calendar slots for %s", len(created), business.slug)

    return created


def _generate_draft(
    business_name: str,
    description: str,
    content_type: str,
    topic: str,
    phase: int,
) -> tuple[str, str]:
    """Generate a structured draft for a content type."""
    phase_context = {
        1: "building your foundation",
        2: "validating your market",
        3: "growing your revenue",
        4: "preparing to scale",
        5: "optimizing for growth",
    }.get(phase, "growing your business")

    if content_type == "blog":
        title = f"{topic}"
        body = (
            f"# {topic}\n\n"
            f"*Draft auto-generated for {business_name} — edit before publishing.*\n\n"
            f"## Introduction\n"
            f"{business_name} is currently focused on {phase_context}. "
            f"This post explores a key insight from that journey.\n\n"
            f"## The Challenge\n"
            f"[Describe the specific challenge your audience faces. "
            f"Use language your customers use — not industry jargon.]\n\n"
            f"## The Approach\n"
            f"[Explain your solution or perspective. Be specific — "
            f"share numbers, timelines, or examples where possible.]\n\n"
            f"## Key Takeaways\n"
            f"1. [First concrete takeaway]\n"
            f"2. [Second concrete takeaway]\n"
            f"3. [Third concrete takeaway]\n\n"
            f"## What's Next\n"
            f"[Call to action — what should the reader do? "
            f"Visit your site, sign up, follow, reply?]\n"
        )
    elif content_type == "social":
        title = f"Social post: {topic[:60]}"
        body = (
            f"Most people think {phase_context} is about doing more.\n\n"
            f"It's actually about doing the right thing, faster.\n\n"
            f"Here's what we learned at {business_name} this week:\n\n"
            f"→ [Key insight 1]\n"
            f"→ [Key insight 2]\n"
            f"→ [Key insight 3]\n\n"
            f"What's your biggest challenge right now?\n\n"
            f"#startup #growth"
        )
    elif content_type == "newsletter":
        title = f"Weekly update from {business_name}"
        body = (
            f"Subject: What's happening at {business_name} this week\n\n"
            f"Hi there,\n\n"
            f"Quick update on what we've been working on:\n\n"
            f"**This week's progress:**\n"
            f"- [Achievement 1]\n"
            f"- [Achievement 2]\n"
            f"- [What we learned]\n\n"
            f"**What's next:**\n"
            f"We're focused on {phase_context}. Next week we'll be tackling "
            f"[next priority].\n\n"
            f"**One thing you can do:**\n"
            f"[CTA — reply, visit, sign up, share]\n\n"
            f"Talk soon,\n"
            f"The {business_name} team\n"
        )
    elif content_type == "report":
        title = f"Monthly report: {business_name}"
        body = (
            f"# Monthly Report: {business_name}\n\n"
            f"**Period:** [Month]\n"
            f"**Phase:** {phase_context.title()}\n\n"
            f"## Key Metrics\n"
            f"| Metric | Last Month | This Month | Change |\n"
            f"|--------|-----------|------------|--------|\n"
            f"| Content produced | — | — | — |\n"
            f"| Website traffic | — | — | — |\n"
            f"| Leads | — | — | — |\n"
            f"| Revenue | — | — | — |\n\n"
            f"## Highlights\n"
            f"- [Top achievement]\n\n"
            f"## Challenges\n"
            f"- [Top challenge]\n\n"
            f"## Next Month Focus\n"
            f"- [Priority 1]\n"
            f"- [Priority 2]\n"
        )
    else:
        title = topic
        body = f"*Draft for {business_name}* — {topic}\n\n[Write your content here]"

    return title, body
