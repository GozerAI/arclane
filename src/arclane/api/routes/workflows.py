"""Workflow routes — list, view, validate, and dry-run .ail workflows."""

from fastapi import APIRouter, HTTPException

from arclane.services.workflow_service import WorkflowService

router = APIRouter()

_service = WorkflowService()


@router.get("/")
async def list_workflows():
    """List available .ail workflow files."""
    return {"workflows": _service.list_workflows()}


@router.get("/{name}")
async def get_workflow(name: str):
    """Get the source of a specific workflow."""
    try:
        source = _service.load_workflow(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {name}")
    return {"name": name, "source": source}


@router.post("/{name}/validate")
async def validate_workflow(name: str):
    """Validate a workflow file."""
    try:
        source = _service.load_workflow(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {name}")

    result = _service.validate_workflow(source)
    return result


@router.post("/{name}/dry-run")
async def dry_run_workflow(name: str):
    """Parse and show what the workflow would do without executing."""
    try:
        source = _service.load_workflow(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {name}")

    if not _service.optimizer_available:
        raise HTTPException(status_code=503, detail="AIL not installed")

    try:
        steps = _service.dry_run(source)
    except SyntaxError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"name": name, "steps": steps}


@router.post("/{name}/tasks")
async def workflow_tasks(name: str, description: str = ""):
    """Convert a workflow into task format (for preview)."""
    if not _service.optimizer_available:
        raise HTTPException(status_code=503, detail="AIL not installed")

    try:
        tasks = _service.workflow_to_tasks(name, description)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {name}")
    except SyntaxError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"name": name, "tasks": tasks, "count": len(tasks)}
