"""Telegram daily steering — sends brief via Telegram bot if configured."""

import httpx

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("telegram_steering")


async def send_steering_telegram(
    chat_id: str,
    brief: dict,
) -> bool:
    """Send the daily steering brief via Telegram.

    Requires ARCLANE_TELEGRAM_BOT_TOKEN in settings.
    chat_id is stored per-business in agent_config.telegram_chat_id.
    """
    bot_token = getattr(settings, "telegram_bot_token", "")
    if not bot_token:
        log.debug("Telegram bot token not configured, skipping")
        return False

    content_lines = ""
    if brief.get("content_produced"):
        items = "\n".join(f"  - {c['title']} ({c['type']})" for c in brief["content_produced"])
        content_lines = f"\n*Produced overnight:*\n{items}"

    milestone_lines = ""
    if brief.get("milestones_hit"):
        items = "\n".join(f"  - {m['title']}" for m in brief["milestones_hit"])
        milestone_lines = f"\n*Milestones completed:*\n{items}"

    health_line = ""
    if brief.get("health_score") is not None:
        health_line = f"\nHealth: {brief['health_score']:.0f}/100"

    message = (
        f"*Day {brief['day']} — {brief['phase']}*\n\n"
        f"{brief['last_cycle_summary']}"
        f"{content_lines}"
        f"{milestone_lines}"
        f"{health_line}\n\n"
        f"*Today:* {brief['today_plan_text']}\n\n"
        f"_{brief['steering_prompt']}_"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
            if resp.status_code == 200:
                log.info("Telegram steering sent to chat %s", chat_id)
                return True
            log.warning("Telegram API returned %d: %s", resp.status_code, resp.text)
            return False
    except Exception:
        log.exception("Telegram steering failed for chat %s", chat_id)
        return False
