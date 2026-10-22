"""Project controller (Layer 3): ONE service call, Result -> HTTP response."""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.responses import from_result
from app.services import project_service
from app.services.project_service import ProjectContext
from app.validators.project import ProjectCreateRequest, ProjectUpdateRequest


def _ctx(request: Request) -> ProjectContext:
    state = request.app.state
    return ProjectContext(state.db_pool, state.settings, state.run_cleanup)


async def create(request: Request, body: ProjectCreateRequest, user: dict) -> JSONResponse:
    return from_result(await project_service.create_project(_ctx(request), user, body.model_dump()), 201)


async def list_mine(request: Request, user: dict) -> JSONResponse:
    return from_result(await project_service.list_projects(_ctx(request), user))


async def get_one(request: Request, project_id: int, user: dict) -> JSONResponse:
    return from_result(await project_service.get_project(_ctx(request), user, project_id))


async def update(request: Request, project_id: int, body: ProjectUpdateRequest, user: dict) -> JSONResponse:
    return from_result(await project_service.update_project(_ctx(request), user, project_id, body.changes()))


async def delete(request: Request, project_id: int, user: dict) -> JSONResponse:
    return from_result(await project_service.delete_project(_ctx(request), user, project_id))
