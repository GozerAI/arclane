"""Provisioning service that turns the operating plan into live infrastructure."""

from copy import deepcopy

from arclane.core.logging import get_logger
from arclane.models.tables import Business
from arclane.provisioning.deploy import deploy_template, wait_for_tenant_surface
from arclane.provisioning.email import provision_email
from arclane.provisioning.subdomain import provision_subdomain

log = get_logger("provisioning")


async def provision_business(business: Business, session=None) -> None:
    """Provision all infrastructure for a new business."""
    log.info("Provisioning business: %s", business.slug)

    try:
        _update_provisioning_step(
            business,
            "subdomain",
            status="running",
            detail="Creating the public route and reserving the tenant hostname.",
        )
        await provision_subdomain(business.slug)
        business.subdomain_provisioned = True
        _update_provisioning_step(
            business,
            "subdomain",
            status="ready",
            detail=f"Public hostname reserved for {business.slug}.",
        )
        log.info("Subdomain provisioned: %s", business.slug)
    except Exception:
        _update_provisioning_step(
            business,
            "subdomain",
            status="failed",
            detail="Subdomain provisioning failed. Review Caddy connectivity.",
        )
        log.exception("Subdomain provisioning failed for %s", business.slug)

    try:
        _update_provisioning_step(
            business,
            "mailbox",
            status="running",
            detail="Configuring the business address and outbound sender identity.",
        )
        await provision_email(business.slug)
        business.email_provisioned = True
        _update_provisioning_step(
            business,
            "mailbox",
            status="ready",
            detail=f"Business address configured for {business.slug}.",
        )
        log.info("Email provisioned: %s@arclane.cloud", business.slug)
    except Exception:
        _update_provisioning_step(
            business,
            "mailbox",
            status="failed",
            detail="Business address provisioning failed. Review email provider configuration.",
        )
        log.exception("Email provisioning failed for %s", business.slug)

    if business.template:
        try:
            _update_provisioning_step(
                business,
                "workspace",
                status="running",
                detail="Creating the tenant workspace from the selected template.",
            )
            _update_provisioning_step(
                business,
                "deploy",
                status="running",
                detail="Building the tenant surface and preparing the live upstream.",
            )
            port, container_id = await deploy_template(
                business.slug,
                business.template,
                session=session,
                business_name=business.name,
                business_description=business.description,
            )
            business.container_port = port
            business.container_id = container_id
            _update_provisioning_step(
                business,
                "workspace",
                status="ready",
                detail="Workspace staged and manifest written.",
            )
            surface_ready = await wait_for_tenant_surface(port)
            business.app_deployed = surface_ready
            _update_provisioning_step(
                business,
                "deploy",
                status="ready" if surface_ready else "failed",
                detail=(
                    f"Tenant surface live on internal port {port}."
                    if surface_ready
                    else f"Workspace staged on internal port {port}, but the live health check did not pass."
                ),
            )
            log.info(
                "App deployment staged: %s (template: %s, port: %d, live=%s)",
                business.slug, business.template, port, surface_ready,
            )
        except Exception:
            _update_provisioning_step(
                business,
                "workspace",
                status="failed",
                detail="Workspace staging failed during template deployment.",
            )
            _update_provisioning_step(
                business,
                "deploy",
                status="failed",
                detail="Deployment failed before the public surface went live.",
            )
            log.exception("App deployment failed for %s", business.slug)
    else:
        _update_provisioning_step(
            business,
            "workspace",
            status="skipped",
            detail="No deployable template selected for this business.",
        )
        _update_provisioning_step(
            business,
            "deploy",
            status="skipped",
            detail="Deployment skipped because no template was selected.",
        )

    log.info(
        "Provisioning complete for %s - subdomain=%s email=%s app=%s",
        business.slug,
        business.subdomain_provisioned,
        business.email_provisioned,
        business.app_deployed,
    )


def _update_provisioning_step(business: Business, key: str, *, status: str, detail: str) -> None:
    """Persist provisioning state inside the stored operating plan."""
    config = deepcopy(business.agent_config or {})
    plan = config.get("operating_plan") or {}
    provisioning = plan.get("provisioning") or {}
    steps = provisioning.get("steps") or []
    for step in steps:
        if step.get("key") == key:
            step["status"] = status
            step["detail"] = detail
            break
    if steps:
        provisioning["steps"] = steps
        plan["provisioning"] = provisioning
        config["operating_plan"] = plan
        business.agent_config = config
