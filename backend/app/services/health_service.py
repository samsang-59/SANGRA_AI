"""Health check: the database is a must, Docker is only a warning (Gaps #3)."""

import logging
from collections.abc import Awaitable, Callable

from app.core.result import Result

logger = logging.getLogger("app.health")


async def check_health(
    database_check: Callable[[], Awaitable[bool]], docker_check: Callable[[], Awaitable[bool]]
) -> Result:
    try:
        database_ok = await database_check()
    except Exception as exc:
        logger.warning("Health: database unreachable (%s)", type(exc).__name__)
        database_ok = False
    if not database_ok:
        return Result.failure("SERVICE_UNAVAILABLE", "The database is not reachable.")

    docker_ok = await docker_check()
    return Result.success({
        "status": "ok" if docker_ok else "degraded",
        "database": "ok",
        "docker": "ok" if docker_ok else "unreachable",
    })
