from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.responses import from_result
from app.services import health_service


async def get_health(request: Request) -> JSONResponse:
    state = request.app.state
    return from_result(await health_service.check_health(state.database_check, state.docker_check))
