"""App factory, startup and shutdown order (Layer 7, item 9).

Startup: load settings -> create the DB pool -> (B9: LangGraph checkpointer) -> check Docker is
reachable (warn if not) -> (B5: startup recovery) -> (B5/B10: background jobs) -> (B9: AgentRunner).
Shutdown runs in reverse.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.background import BackgroundTasks
from app.core.config import Settings, load_settings
from app.core.db import close_pool, create_pool, open_pool
from app.core.docker_check import docker_reachable
from app.core.errors import install_error_handlers
from app.core.mailer import build_mailer
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, upload_aware_limit
from app.core.rate_limit import RateLimiter, auth_rules
from app.core.security import DummyHash
from app.core.singleflight import SingleFlight
from app.repositories import health_repository
from app.routers import auth, health, projects
from app.services.cleanup_service import RunCleanup

logger = logging.getLogger("app.main")

API_PREFIX = "/api/v1"
CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type", "Last-Event-ID"]
CORS_EXPOSED_HEADERS = ["X-Request-ID", "Content-Disposition"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    pool = create_pool(settings)
    await open_pool(pool)
    app.state.db_pool = pool
    app.state.database_check = SingleFlight(partial(health_repository.ping, pool))
    try:
        if not await app.state.docker_check():
            logger.warning("Docker is not reachable: test runs will fail until it is started")
        logger.info("SANGRA_AI started (mode=%s)", settings.app_mode)
        yield
    finally:
        await app.state.background.drain()
        await close_pool(pool)
        logger.info("SANGRA_AI stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="SANGRA_AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.mailer = build_mailer(settings)
    app.state.background = BackgroundTasks()
    app.state.run_cleanup = RunCleanup(settings.packages_dir)
    app.state.rate_limiter = RateLimiter()
    app.state.rate_rules = auth_rules(settings)
    app.state.dummy_hash = DummyHash(settings.bcrypt_rounds)
    app.state.docker_check = SingleFlight(partial(docker_reachable, settings.docker_check_timeout_seconds))

    install_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(projects.router, prefix=API_PREFIX)

    # Added inner to outer: body limit, then request id / error catcher, then CORS (outermost,
    # so even 413 and 500 answers carry the CORS headers).
    app.add_middleware(
        BodySizeLimitMiddleware,
        limit_for=upload_aware_limit(settings.max_body_bytes, settings.max_upload_bytes),
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=CORS_ALLOWED_HEADERS,
        expose_headers=CORS_EXPOSED_HEADERS,
        allow_credentials=False,
    )
    return app
