"""Provisioning service — orchestrates all setup steps for a new business."""

from arclane.core.logging import get_logger
from arclane.models.tables import Business
from arclane.provisioning.subdomain import provision_subdomain
from arclane.provisioning.email import provision_email
from arclane.provisioning.deploy import deploy_template

log = get_logger("provisioning")


async def provision_business(business: Business, session=None) -> None:
    """Provision all infrastructure for a new business.

    Each step is independent and logs failures without blocking others.
    Operates on the business object directly — caller is responsible
    for committing changes to the database.
    """
    log.info("Provisioning business: %s", business.slug)

    # 1. Subdomain
    try:
        await provision_subdomain(business.slug)
        business.subdomain_provisioned = True
        log.info("Subdomain provisioned: %s", business.slug)
    except Exception:
        log.exception("Subdomain provisioning failed for %s", business.slug)

    # 2. Email
    try:
        await provision_email(business.slug)
        business.email_provisioned = True
        log.info("Email provisioned: %s@arclane.cloud", business.slug)
    except Exception:
        log.exception("Email provisioning failed for %s", business.slug)

    # 3. App template deployment
    if business.template:
        try:
            port, container_id = await deploy_template(
                business.slug, business.template, session=session,
            )
            business.app_deployed = True
            business.container_port = port
            business.container_id = container_id
            log.info(
                "App deployed: %s (template: %s, port: %d)",
                business.slug, business.template, port,
            )
        except Exception:
            log.exception("App deployment failed for %s", business.slug)

    log.info(
        "Provisioning complete for %s — subdomain=%s email=%s app=%s",
        business.slug,
        business.subdomain_provisioned,
        business.email_provisioned,
        business.app_deployed,
    )
