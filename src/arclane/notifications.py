"""Email notifications for business lifecycle events."""

from arclane.core.logging import get_logger

log = get_logger("notifications")

FROM_SLUG = "arclane"


async def send_welcome_email(business_name: str, owner_email: str, slug: str) -> None:
    """Send welcome email after business creation with next steps."""
    from arclane.provisioning.email import send_email

    subject = f"Welcome to Arclane — {business_name} is live!"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #1a1a2e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Welcome to Arclane</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>Your business <strong>{business_name}</strong> has been created and your first AI cycle
    is already running. Here's what happens next:</p>
    <ol style="line-height: 1.8;">
      <li>Our AI executives analyze your business and generate an initial strategy</li>
      <li>Content, operations, and security recommendations are produced</li>
      <li>Everything lands on your dashboard — no setup needed</li>
    </ol>
    <p>Your dashboard is ready at:</p>
    <p style="text-align: center;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #4f46e5; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Open Dashboard
      </a>
    </p>
    <p style="color: #666; font-size: 14px; margin-top: 24px;">
      If you have questions, just reply to this email.
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Welcome email sent to %s for business %s", owner_email, slug)
    except Exception:
        log.exception("Failed to send welcome email to %s for business %s", owner_email, slug)


# Subject lines and preview snippets for Day 1 per-task status emails
_TASK_EMAIL_CONFIG: dict[str, dict[str, str]] = {
    "core-strategy-01": {
        "subject": "Your strategy brief is ready",
        "headline": "Strategy Brief Complete",
        "preview": "We analyzed your business and produced a positioning brief with mission, offer, target customer, and competitive wedge.",
        "color": "#4f46e5",
    },
    "core-market-01": {
        "subject": "Market research complete",
        "headline": "Market Research Report",
        "preview": "Competitors mapped, demand signals identified, and positioning gaps uncovered. Your market landscape is ready.",
        "color": "#0891b2",
    },
    "core-content-01": {
        "subject": "Your landing page draft is ready",
        "headline": "Landing Page Draft",
        "preview": "A conversion-ready landing page with headline, offer, proof, and CTA — built from your strategy and market research.",
        "color": "#059669",
    },
    "core-social-01": {
        "subject": "You've been announced on Twitter",
        "headline": "Launch Tweet Published",
        "preview": "We just announced your business on the Arclane Twitter account. Your Day 1 package is complete.",
        "color": "#7c3aed",
    },
}


async def send_task_complete_email(
    business_name: str,
    owner_email: str,
    slug: str,
    task_key: str,
    task_index: int,
    task_total: int,
    result_snippet: str | None = None,
) -> None:
    """Send a per-task status email during the initial cycle.

    Each Day 1 deliverable gets its own email so the user sees value
    arriving in real time as the cycle executes.
    """
    from arclane.provisioning.email import send_email

    config = _TASK_EMAIL_CONFIG.get(task_key)
    if not config:
        return  # Only send emails for configured tasks

    subject = f"{config['subject']} — {business_name}"
    color = config["color"]
    progress_pct = round((task_index / max(task_total, 1)) * 100)

    snippet_html = ""
    if result_snippet:
        # Show first ~200 chars of the deliverable as a preview
        preview_text = result_snippet[:200].replace("<", "&lt;").replace(">", "&gt;")
        if len(result_snippet) > 200:
            preview_text += "..."
        snippet_html = (
            f'<div style="background: #f9fafb; border-left: 4px solid {color}; '
            f'padding: 12px; margin: 16px 0; font-size: 14px; color: #374151; '
            f'white-space: pre-wrap;">{preview_text}</div>'
        )

    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: {color}; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">{config['headline']}</h1>
    <p style="color: rgba(255,255,255,0.8); margin: 8px 0 0; font-size: 14px;">
      Step {task_index} of {task_total} · Day 1
    </p>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>{config['preview']}</p>
    {snippet_html}
    <div style="background: #f3f4f6; border-radius: 8px; padding: 12px; margin: 16px 0;">
      <div style="background: #e5e7eb; border-radius: 4px; overflow: hidden;">
        <div style="background: {color}; height: 8px; width: {progress_pct}%; border-radius: 4px;"></div>
      </div>
      <p style="margin: 8px 0 0; font-size: 12px; color: #6b7280; text-align: center;">
        {progress_pct}% of your Day 1 package complete
      </p>
    </div>
    <p style="text-align: center;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: {color}; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        View on Dashboard
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Task status email sent to %s: %s", owner_email, task_key)
    except Exception:
        log.exception("Failed to send task status email to %s: %s", owner_email, task_key)


async def send_cycle_complete_email(
    business_name: str,
    owner_email: str,
    slug: str,
    tasks_completed: int,
    tasks_total: int,
) -> None:
    """Send notification when a cycle finishes."""
    from arclane.provisioning.email import send_email

    subject = f"Cycle complete — {tasks_completed}/{tasks_total} tasks done for {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #1a1a2e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Cycle Complete</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>A cycle just finished for <strong>{business_name}</strong>.</p>
    <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; margin: 16px 0;">
      <p style="margin: 0; font-size: 18px; text-align: center;">
        <strong>{tasks_completed}</strong> of <strong>{tasks_total}</strong> tasks completed successfully
      </p>
    </div>
    <p>New content and insights are waiting on your dashboard:</p>
    <p style="text-align: center;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #4f46e5; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        View Results
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Cycle complete email sent to %s for business %s", owner_email, slug)
    except Exception:
        log.exception("Failed to send cycle complete email to %s for business %s", owner_email, slug)


async def send_working_days_low_email(
    business_name: str, owner_email: str, working_days_remaining: int
) -> None:
    """Send warning when working days drop to 2 or below."""
    from arclane.provisioning.email import send_email

    subject = f"Low working days — {working_days_remaining} remaining for {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #92400e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Credits Running Low</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>Your business <strong>{business_name}</strong> has only
    <strong>{working_days_remaining}</strong> working day{"s" if working_days_remaining != 1 else ""} remaining.</p>
    <p>When working days run out, scheduled cycles will be paused. Upgrade your plan to keep
    your AI executives running:</p>
    <p style="text-align: center;">
      <a href="https://arclane.cloud/billing"
         style="display: inline-block; background: #dc2626; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Upgrade Plan
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Low working days email sent to %s (%d remaining)", owner_email, working_days_remaining)
    except Exception:
        log.exception("Failed to send low working days email to %s", owner_email)


async def send_password_reset_email(email: str, reset_token: str) -> None:
    """Send password reset email with a tokenized link."""
    from arclane.provisioning.email import send_email

    subject = "Reset your Arclane password"
    reset_url = f"https://arclane.cloud/reset-password?token={reset_token}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #1a1a2e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Password Reset</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>We received a request to reset your password. Click the button below to choose a new one:</p>
    <p style="text-align: center;">
      <a href="{reset_url}"
         style="display: inline-block; background: #4f46e5; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Reset Password
      </a>
    </p>
    <p style="color: #666; font-size: 14px; margin-top: 24px;">
      If you didn't request this, you can safely ignore this email. The link expires in 1 hour.
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, email, subject, body)
        log.info("Password reset email sent to %s", email)
    except Exception:
        log.exception("Failed to send password reset email to %s", email)


async def send_mailbox_ready_email(
    business_name: str,
    owner_email: str,
    slug: str,
    mailbox: str,
) -> None:
    """Send notification when the business address is configured."""
    from arclane.provisioning.email import send_email

    subject = f"Business address configured - {mailbox} is active for {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #0f766e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Business Address Configured</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>Your Arclane business address for <strong>{business_name}</strong> is now configured:</p>
    <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; margin: 16px 0; text-align: center;">
      <strong style="font-size: 18px;">{mailbox}</strong>
    </div>
    <p>Arclane can now use this address as the business contact identity tied to your workspace.</p>
    <p style="text-align: center;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #0f766e; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Open Dashboard
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Business address email sent to %s for %s", owner_email, slug)
    except Exception:
        log.exception("Failed to send business address email to %s for %s", owner_email, slug)


async def send_weekly_digest_email(
    business_name: str,
    owner_email: str,
    slug: str,
    digest: dict,
) -> None:
    """Send weekly digest email summarizing the past week's progress."""
    from arclane.provisioning.email import send_email

    cycles_completed = digest.get("cycles", {}).get("completed", 0)
    cycles_total = digest.get("cycles", {}).get("total", 0)
    content_produced = digest.get("content", {}).get("produced", 0)
    milestones_completed = digest.get("milestones", {}).get("completed", 0)
    milestone_names = digest.get("milestones", {}).get("names", [])
    weekly_usd = digest.get("revenue", {}).get("weekly_usd", 0)
    roadmap_day = digest.get("roadmap_day", 0)
    current_phase = digest.get("current_phase", 0)

    phase_label = f"Phase {current_phase}" if current_phase <= 4 else "Forever Partner"
    milestones_html = ""
    if milestone_names:
        milestones_html = "<ul>" + "".join(f"<li>{name}</li>" for name in milestone_names) + "</ul>"

    notes_html = ""
    top_notes = digest.get("top_notes", [])
    if top_notes:
        notes_html = "<h3 style='margin-top:16px;'>Action Items</h3><ul>"
        for note in top_notes[:3]:
            notes_html += f"<li><strong>[{note['category'].upper()}]</strong> {note['title']}</li>"
        notes_html += "</ul>"

    subject = f"Week in review — Day {roadmap_day}, {phase_label} | {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #1a1a2e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Weekly Digest</h1>
    <p style="color: #a5b4fc; margin: 8px 0 0;">Day {roadmap_day} · {phase_label}</p>
  </div>
  <div style="padding: 24px;">
    <p>Hi there, here's what happened for <strong>{business_name}</strong> this week:</p>

    <div style="display: flex; gap: 12px; margin: 16px 0;">
      <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; flex: 1; text-align: center;">
        <div style="font-size: 24px; font-weight: bold;">{cycles_completed}/{cycles_total}</div>
        <div style="font-size: 12px; color: #666;">Cycles</div>
      </div>
      <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; flex: 1; text-align: center;">
        <div style="font-size: 24px; font-weight: bold;">{content_produced}</div>
        <div style="font-size: 12px; color: #666;">Content</div>
      </div>
      <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; flex: 1; text-align: center;">
        <div style="font-size: 24px; font-weight: bold;">{milestones_completed}</div>
        <div style="font-size: 12px; color: #666;">Milestones</div>
      </div>
      <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; flex: 1; text-align: center;">
        <div style="font-size: 24px; font-weight: bold;">${weekly_usd:,.0f}</div>
        <div style="font-size: 12px; color: #666;">Revenue</div>
      </div>
    </div>

    {f"<h3>Milestones Completed</h3>{milestones_html}" if milestones_html else ""}
    {notes_html}

    <p style="text-align: center; margin-top: 24px;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #4f46e5; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Open Dashboard
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Weekly digest sent to %s for %s", owner_email, slug)
    except Exception:
        log.exception("Failed to send weekly digest to %s for %s", owner_email, slug)


async def send_phase_advancement_email(
    business_name: str,
    owner_email: str,
    slug: str,
    from_phase: int,
    to_phase: int,
    score: float,
) -> None:
    """Send notification when a business advances to a new phase or graduates."""
    from arclane.provisioning.email import send_email

    phase_names = {1: "Foundation", 2: "Validation", 3: "Growth", 4: "Scale-Ready", 5: "Graduated"}
    to_name = phase_names.get(to_phase, f"Phase {to_phase}")

    if to_phase >= 5:
        headline = "Congratulations — You've Graduated!"
        message = (
            f"<strong>{business_name}</strong> has completed the 90-day incubator program "
            f"with a graduation score of <strong>{score:.0f}%</strong>."
        )
        cta_text = "Enter Forever Partner Mode"
        color = "#059669"
    else:
        headline = f"Phase {to_phase}: {to_name}"
        message = (
            f"<strong>{business_name}</strong> has advanced to <strong>{to_name}</strong> "
            f"with a Phase {from_phase} score of <strong>{score:.0f}%</strong>."
        )
        cta_text = "See Your New Milestones"
        color = "#4f46e5"

    subject = f"{business_name} — {headline}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: {color}; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">{headline}</h1>
  </div>
  <div style="padding: 24px;">
    <p>{message}</p>
    <p style="text-align: center; margin-top: 24px;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: {color}; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        {cta_text}
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Phase advancement email sent to %s (%s → %s)", owner_email, from_phase, to_phase)
    except Exception:
        log.exception("Failed to send phase advancement email to %s", owner_email)


async def send_milestone_celebration_email(
    business_name: str,
    owner_email: str,
    slug: str,
    milestone_title: str,
    roadmap_day: int,
) -> None:
    """Send a brief celebration email when a key milestone is completed."""
    from arclane.provisioning.email import send_email

    subject = f"Milestone achieved — {milestone_title} | {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #059669; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Milestone Achieved</h1>
  </div>
  <div style="padding: 24px;">
    <p><strong>{business_name}</strong> just completed:</p>
    <div style="background: #f0fdf4; border-left: 4px solid #059669; padding: 16px; margin: 16px 0;">
      <strong style="font-size: 16px;">{milestone_title}</strong>
      <br><span style="color: #666;">Day {roadmap_day} of your 90-day program</span>
    </div>
    <p style="text-align: center;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #059669; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        View Progress
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Milestone email sent to %s: %s", owner_email, milestone_title)
    except Exception:
        log.exception("Failed to send milestone email to %s", owner_email)


async def send_daily_steering_email(
    business_name: str,
    to_email: str,
    slug: str,
    brief: dict,
) -> None:
    """Send the daily steering/decisioning email."""
    from arclane.provisioning.email import send_email

    content_list = ""
    if brief["content_produced"]:
        items = "".join(
            f"<li><strong>{c['title']}</strong> ({c['type']})</li>"
            for c in brief["content_produced"]
        )
        content_list = f"<h3>Produced overnight</h3><ul>{items}</ul>"

    milestones_list = ""
    if brief["milestones_hit"]:
        items = "".join(f"<li>{m['title']}</li>" for m in brief["milestones_hit"])
        milestones_list = f"<h3>Milestones completed</h3><ul>{items}</ul>"

    health_text = ""
    if brief["health_score"] is not None:
        health_text = f"<p><strong>Health score:</strong> {brief['health_score']:.0f}/100</p>"

    html = f"""\
<html>
<body>
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; color: #1a1a2e;">
        <h2>Good morning — Day {brief['day']}, {brief['phase']}</h2>
        <p>{brief['last_cycle_summary']}</p>
        {content_list}
        {milestones_list}
        {health_text}
        <h3>Today's plan</h3>
        <p>{brief['today_plan_text']}</p>
        <div style="background: #f0f4ff; border-radius: 12px; padding: 16px; margin: 20px 0;">
            <p style="margin: 0; font-weight: 600;">Your turn</p>
            <p style="margin: 8px 0 0;">{brief['steering_prompt']}</p>
        </div>
        <p>
            <a href="https://arclane.cloud/dashboard"
               style="display: inline-block; padding: 12px 24px; background: #4bb2ff;
                      color: white; border-radius: 999px; text-decoration: none; font-weight: 700;">
                Open Dashboard
            </a>
        </p>
        <p style="color: #888; font-size: 13px; margin-top: 24px;">
            {business_name} — Arclane Incubator, Day {brief['day']}
        </p>
    </div>
</body>
</html>"""

    subject = f"Day {brief['day']} — {brief['phase']}: Here's what happened, what's next"

    try:
        await send_email(FROM_SLUG, to_email, subject, html)
        log.info("Daily steering email sent to %s for %s", to_email, slug)
    except Exception:
        log.exception("Failed to send daily steering email to %s for %s", to_email, slug)


async def send_urgent_advisory_email(
    business_name: str,
    owner_email: str,
    slug: str,
    notes: list[dict],
) -> None:
    """Send email for high-priority advisory warnings."""
    from arclane.provisioning.email import send_email

    if not notes:
        return

    items_html = ""
    for note in notes[:5]:
        cat = note.get("category", "").upper()
        items_html += (
            f'<div style="background: #fef2f2; border-left: 4px solid #dc2626; '
            f'padding: 12px; margin: 8px 0;">'
            f'<strong>[{cat}]</strong> {note.get("title", "")}<br>'
            f'<span style="color: #666;">{note.get("body", "")}</span></div>'
        )

    subject = f"Action needed — {len(notes)} advisory alert{'s' if len(notes) > 1 else ''} for {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #dc2626; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Advisory Alerts</h1>
  </div>
  <div style="padding: 24px;">
    <p><strong>{business_name}</strong> has {len(notes)} item{"s" if len(notes) > 1 else ""} that need your attention:</p>
    {items_html}
    <p style="text-align: center; margin-top: 24px;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #dc2626; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Review & Acknowledge
      </a>
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Urgent advisory email sent to %s (%d notes)", owner_email, len(notes))
    except Exception:
        log.exception("Failed to send urgent advisory email to %s", owner_email)


async def send_preview_welcome_email(
    owner_email: str, business_name: str, slug: str
) -> None:
    """Send welcome email immediately on preview signup."""
    from arclane.provisioning.email import send_email

    subject = "Your first cycle is running"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #4f46e5; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Your First Cycle Is Running</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>Welcome to Arclane. Your business <strong>{business_name}</strong> is live and your
    first AI cycle is already building four deliverables:</p>
    <ol style="line-height: 1.8;">
      <li><strong>Strategy brief</strong> — positioning, mission, and competitive wedge</li>
      <li><strong>Market research</strong> — competitors, demand signals, and gaps</li>
      <li><strong>Landing page</strong> — conversion-ready draft with headline, offer, and CTA</li>
      <li><strong>Launch tweet</strong> — your public announcement</li>
    </ol>
    <p>Day 1 is free — no credit card needed. To keep building after today, choose a plan.
    You'll get a 48-hour trial to cancel before you're charged.</p>
    <p style="text-align: center;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #4f46e5; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Open Dashboard
      </a>
    </p>
    <p style="color: #666; font-size: 14px; margin-top: 24px;">
      Results will arrive within minutes. We'll email you as each deliverable completes.
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Preview welcome email sent to %s for %s", owner_email, slug)
    except Exception:
        log.exception("Failed to send preview welcome email to %s for %s", owner_email, slug)


async def send_preview_results_email(
    owner_email: str, business_name: str, slug: str, highlights: list[str]
) -> None:
    """Send results email on Day 2 when market research highlights are ready."""
    from arclane.provisioning.email import send_email

    highlights_html = ""
    if highlights:
        items = "".join(
            f"<li style=\"margin-bottom: 8px;\">{h}</li>" for h in highlights
        )
        highlights_html = (
            f'<ul style="line-height: 1.8; padding-left: 20px;">{items}</ul>'
        )

    subject = f"Your market research is in \u2014 {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #0891b2; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Your Results Are Ready</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>Your Day 1 deliverables for <strong>{business_name}</strong> are complete. Here are some
    highlights from your market research:</p>
    {highlights_html}
    <p style="text-align: center;">
      <a href="https://{slug}.arclane.cloud/dashboard"
         style="display: inline-block; background: #0891b2; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        View Full Results
      </a>
    </p>
    <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; margin: 24px 0;">
      <p style="margin: 0; font-size: 14px; color: #374151;">
        Tomorrow is your last preview day. To keep your AI executives running,
        <strong>Starter</strong> is just <strong>$49/mo</strong> for 10 working days per month.
      </p>
    </div>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Preview results email sent to %s for %s", owner_email, slug)
    except Exception:
        log.exception("Failed to send preview results email to %s for %s", owner_email, slug)


async def send_preview_upgrade_email(
    owner_email: str, business_name: str, slug: str
) -> None:
    """Send upgrade nudge on Day 4 if the user hasn't converted from preview."""
    from arclane.provisioning.email import send_email

    subject = "Your preview ended \u2014 here's what's next"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #7c3aed; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Your Preview Has Ended</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>In just 3 days, Arclane delivered the following for <strong>{business_name}</strong>:</p>
    <ul style="line-height: 1.8;">
      <li>Strategy brief with positioning, mission, and competitive wedge</li>
      <li>Market research with competitor mapping and demand signals</li>
      <li>Conversion-ready landing page draft</li>
      <li>Public launch tweet</li>
    </ul>
    <p>Pick a plan to keep going:</p>
    <div style="display: flex; gap: 12px; margin: 16px 0;">
      <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; flex: 1; text-align: center;">
        <div style="font-size: 20px; font-weight: bold; color: #7c3aed;">Starter</div>
        <div style="font-size: 24px; font-weight: bold; margin: 8px 0;">$49<span style="font-size: 14px; color: #666;">/mo</span></div>
        <div style="font-size: 13px; color: #666;">10 working days/month</div>
      </div>
      <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; flex: 1; text-align: center; border: 2px solid #7c3aed;">
        <div style="font-size: 20px; font-weight: bold; color: #7c3aed;">Pro</div>
        <div style="font-size: 24px; font-weight: bold; margin: 8px 0;">$99<span style="font-size: 14px; color: #666;">/mo</span></div>
        <div style="font-size: 13px; color: #666;">20 working days/month + advanced analytics</div>
      </div>
    </div>
    <p style="text-align: center; margin-top: 24px;">
      <a href="https://arclane.cloud/pricing"
         style="display: inline-block; background: #7c3aed; color: #fff; padding: 12px 24px;
                border-radius: 6px; text-decoration: none; font-weight: bold;">
        Choose a Plan
      </a>
    </p>
    <p style="color: #666; font-size: 14px; margin-top: 24px;">
      &mdash; Arclane
    </p>
  </div>
</body>
</html>"""

    try:
        await send_email(FROM_SLUG, owner_email, subject, body)
        log.info("Preview upgrade email sent to %s for %s", owner_email, slug)
    except Exception:
        log.exception("Failed to send preview upgrade email to %s for %s", owner_email, slug)
