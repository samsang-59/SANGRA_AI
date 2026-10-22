"""Project routes (Layer 1). All protected."""

from fastapi import APIRouter, Depends, Request

from app.controllers import project_controller
from app.core.auth import get_current_user
from app.validators.project import EntityId, ProjectCreateRequest, ProjectUpdateRequest

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", status_code=201)
async def create_project(request: Request, body: ProjectCreateRequest, user: dict = Depends(get_current_user)):
    return await project_controller.create(request, body, user)


@router.get("")
async def list_projects(request: Request, user: dict = Depends(get_current_user)):
    return await project_controller.list_mine(request, user)


@router.get("/{project_id}")
async def get_project(request: Request, project_id: EntityId, user: dict = Depends(get_current_user)):
    return await project_controller.get_one(request, project_id, user)


@router.patch("/{project_id}")
async def update_project(request: Request, project_id: EntityId, body: ProjectUpdateRequest,
                         user: dict = Depends(get_current_user)):
    return await project_controller.update(request, project_id, body, user)


@router.delete("/{project_id}")
async def delete_project(request: Request, project_id: EntityId, user: dict = Depends(get_current_user)):
    return await project_controller.delete(request, project_id, user)
