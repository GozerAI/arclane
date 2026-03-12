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


async def send_credits_low_email(
    business_name: str, owner_email: str, credits_remaining: int
) -> None:
    """Send warning when credits drop to 2 or below."""
    from arclane.provisioning.email import send_email

    subject = f"Low credits — {credits_remaining} remaining for {business_name}"
    body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
  <div style="background: #92400e; padding: 24px; text-align: center;">
    <h1 style="color: #fff; margin: 0; font-size: 24px;">Credits Running Low</h1>
  </div>
  <div style="padding: 24px;">
    <p>Hi there,</p>
    <p>Your business <strong>{business_name}</strong> has only
    <strong>{credits_remaining}</strong> credit{"s" if credits_remaining != 1 else ""} remaining.</p>
    <p>When credits run out, scheduled cycles will be paused. Upgrade your plan to keep
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
        log.info("Low credits email sent to %s (%d remaining)", owner_email, credits_remaining)
    except Exception:
        log.exception("Failed to send low credits email to %s", owner_email)


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
