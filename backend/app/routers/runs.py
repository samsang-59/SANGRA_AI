"""Run routes (Layer 1). All protected."""

from fastapi import APIRouter, Depends, Request

from app.controllers import run_controller
from app.core.auth import get_current_user
from app.validators.project import EntityId
from app.validators.run import ApprovalRequest, HintRequest, RunListQuery, run_list_query

router = APIRouter(tags=["runs"])


@router.post("/projects/{project_id}/runs", status_code=201)
async def start_run(request: Request, project_id: EntityId, user: dict = Depends(get_current_user)):
    """Body: JSON {"bug_description"}; for zip projects multipart/form-data with bug_description and zip_file."""
    return await run_controller.start(request, project_id, user)


@router.get("/runs")
async def list_runs(request: Request, query: RunListQuery = Depends(run_list_query),
                    user: dict = Depends(get_current_user)):
    return await run_controller.list_mine(request, query, user)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: EntityId, user: dict = Depends(get_current_user)):
    return await run_controller.get_one(request, run_id, user)


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: EntityId, user: dict = Depends(get_current_user)):
    """Live progress (Server-Sent Events). Optional header Last-Event-ID: the last event number already seen."""
    return await run_controller.stream(request, run_id, user)


@router.post("/runs/{run_id}/approval")
async def approval(request: Request, run_id: EntityId, body: ApprovalRequest, user: dict = Depends(get_current_user)):
    return await run_controller.approval(request, run_id, body, user)


@router.post("/runs/{run_id}/hint")
async def hint(request: Request, run_id: EntityId, body: HintRequest, user: dict = Depends(get_current_user)):
    return await run_controller.hint(request, run_id, body, user)


@router.post("/runs/{run_id}/cancel")
async def cancel(request: Request, run_id: EntityId, user: dict = Depends(get_current_user)):
    return await run_controller.cancel(request, run_id, user)


@router.post("/runs/{run_id}/apply-to-source")
async def apply_to_source(request: Request, run_id: EntityId, user: dict = Depends(get_current_user)):
    """"Apply to my folder" (local mode, local folder projects, succeeded runs, once)."""
    return await run_controller.apply_to_source(request, run_id, user)


@router.get("/runs/{run_id}/package")
async def download_package(request: Request, run_id: EntityId, user: dict = Depends(get_current_user)):
    """The download package (a zip). 410 PACKAGE_EXPIRED after 7 days."""
    return await run_controller.package(request, run_id, user)


@router.delete("/runs/{run_id}")
async def delete_run(request: Request, run_id: EntityId, user: dict = Depends(get_current_user)):
    return await run_controller.delete(request, run_id, user)
