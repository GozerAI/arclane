"""Workflow routes — requires commercial license.

Visit https://gozerai.com/pricing for details.
"""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/")
async def list_workflows():
    """Workflow management requires a commercial Arclane license."""
    return {"workflows": [], "message": "Workflow features require a commercial license. Visit https://gozerai.com/pricing"}
