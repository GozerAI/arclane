"""Email provisioning — catch-all routing on arclane.cloud.

Uses a catch-all domain setup where *@arclane.cloud is received,
and we route based on the local part (slug) to the right business.
Outbound email uses Resend with per-business from addresses.
"""

from arclane.core.config import settings
from arclane.core.logging import get_logger

log = get_logger("provisioning.email")


async def provision_email(slug: str) -> None:
    """Register email routing for a business.

    With a catch-all, this is mostly bookkeeping — we record that
    {slug}@arclane.cloud is active so inbound routing knows about it.

    Actual email sending goes through Resend with From: {slug}@arclane.cloud.
    """
    email_address = f"{slug}@{settings.email_from_domain}"
    log.info("Email address registered: %s", email_address)


async def send_email(
    from_slug: str,
    to: str,
    subject: str,
    body: str,
) -> dict:
    """Send an email on behalf of a business via Resend."""
    import httpx

    from_address = f"{from_slug}@{settings.email_from_domain}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": from_address,
                "to": to,
                "subject": subject,
                "html": body,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()
