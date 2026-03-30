"""Ebook production status — delegates to Content Production service."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from arclane.api.deps import get_business
from arclane.core.database import get_session
from arclane.core.logging import get_logger
from arclane.integrations.content_production_client import ContentProductionClient
from arclane.models.tables import Business

log = get_logger("routes.ebooks")

router = APIRouter()


def _get_cp_client() -> ContentProductionClient:
    """Return a CP client instance using default env config."""
    return ContentProductionClient()


@router.get("/status")
async def ebook_production_status(
    business: Business = Depends(get_business),
    session: AsyncSession = Depends(get_session),
):
    """Get ebook production status from Content Production for this business.

    Fetches the production job list and filters for jobs whose topic
    relates to this business (by name or slug match in topic text).
    """
    client = _get_cp_client()
    jobs = client.get_job_status()

    if jobs is None:
        return {
            "status": "unavailable",
            "message": "Content Production service is not reachable",
            "jobs": [],
        }

    # Filter jobs relevant to this business by checking topic text
    biz_terms = {business.name.lower(), business.slug.lower()}

    def _is_relevant(job: dict) -> bool:
        topic = (job.get("topic") or job.get("description") or "").lower()
        # Also match if the job was submitted with this business's category/audience
        audience = (job.get("audience") or "").lower()
        for term in biz_terms:
            if term in topic or term in audience:
                return True
        return False

    filtered = {
        "active": [j for j in (jobs.get("active") or []) if _is_relevant(j)],
        "queued": [j for j in (jobs.get("queued") or []) if _is_relevant(j)],
        "completed": [j for j in (jobs.get("completed") or []) if _is_relevant(j)],
    }

    total = len(filtered["active"]) + len(filtered["queued"]) + len(filtered["completed"])

    return {
        "status": "ok",
        "total_jobs": total,
        "jobs": filtered,
    }
