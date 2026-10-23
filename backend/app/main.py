"""App factory, startup and shutdown order (Layer 7, item 9).

Startup: load settings -> create the DB pool -> set up the LangGraph checkpointer (its own connections) ->
check Docker is reachable (warn if not) and remove leftover test containers -> create the AgentRunner and
connect the services -> startup recovery -> start the background jobs. (The runner is created before the recovery because the recovery asks it
which runs it is running; at that moment it runs nothing.) Shutdown runs in reverse.
"""

import asyncio
import gc
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.fakes import fake_parts
from app.agent.llm.client import GroqChatModel
from app.agent.nodes.steps import AgentServices
from app.agent.runner import AgentRunner
from app.agent.workflow import LangGraphWorkflow, create_checkpointer_pool
from app.core import clock
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
from app.routers import auth, health, projects, runs
from app.runners.test_runner import TestRunner
from app.runners.workspace_manager import WorkspaceManager
from app.services.cleanup_service import RunCleanup
from app.services.jobs import JobScheduler
from app.services.package_service import build_run_package
from app.services.run_progress import RunProgress

logger = logging.getLogger("app.main")

API_PREFIX = "/api/v1"
CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type", "Last-Event-ID"]
CORS_EXPOSED_HEADERS = ["X-Request-ID", "Content-Disposition"]
UPLOAD_FORM_OVERHEAD = 1024 * 1024


def build_workflow(app: FastAPI, progress: RunProgress, checkpointer: AsyncPostgresSaver) -> LangGraphWorkflow:
    """The agent: the LangGraph graph, with Groq and the Docker test runner (or the scripted ones of a test setup)."""
    settings: Settings = app.state.settings
    if settings.agent_test_mode == "fake":
        llm, test_runner = fake_parts(settings.agent_fake_script)
        model = lambda: llm  # noqa: E731
    else:
        test_runner = app.state.test_runner
        model = lambda: GroqChatModel(settings)  # noqa: E731  (a client is built for each step)
    services = AgentServices(progress, app.state.workspace_manager, test_runner, model, settings)
    return LangGraphWorkflow(services, checkpointer)


def register_finish_steps(app: FastAPI, progress: RunProgress, runner: AgentRunner,
                          workflow: LangGraphWorkflow) -> None:
    """What happens when a run ends (by the agent, a cancel, the timeout or a restart), in this order
    (04_backend_design.md 6.4 step 11): undo what is still changed (unless the run succeeded) and build the package;
    then the final status is saved; then the temporary copy is deleted and the LangGraph snapshot is deleted (by the
    runner after the graph has ended, or at once if no graph runs for the run)."""
    workspaces: WorkspaceManager = app.state.workspace_manager
    settings: Settings = app.state.settings

    async def undo_unless_succeeded(run: dict, final_status: str, stop_reason, summary: str) -> None:
        code = Path(run["workspace_path"]) / "code" if run.get("workspace_path") else None
        if final_status != "succeeded" and code is not None and (code / ".git").exists():
            try:
                await asyncio.to_thread(workspaces.undo, code)
            except Exception as exc:
                logger.warning("Run %s: undo at the end failed (%s)", run["id"], type(exc).__name__)

    async def package(run: dict, final_status: str, stop_reason, summary: str) -> None:
        await build_run_package(app.state.db_pool, settings, workspaces, run, final_status, stop_reason, summary)

    async def delete_workspace(run: dict) -> None:
        if run.get("workspace_path"):
            await asyncio.to_thread(workspaces.delete, Path(run["workspace_path"]))
            await progress.mark_workspace_deleted(run["id"])

    async def delete_snapshot_if_idle(run: dict) -> None:
        if not runner.is_running(run["id"]):
            await workflow.delete_snapshots(run["id"])

    async def delete_snapshot_after_end(run_id: int) -> None:
        run = await progress.load_run(run_id)
        if run is None or run["status"] in ("succeeded", "failed", "stopped"):
            await workflow.delete_snapshots(run_id)

    progress.before_final_hooks.extend([undo_unless_succeeded, package])
    progress.finish_hooks.extend([delete_workspace, delete_snapshot_if_idle])
    progress.stop_agent = runner.cancel
    runner.after_end_hooks.append(delete_snapshot_after_end)
    app.state.run_cleanup.thread_deleters.append(workflow.delete_snapshots)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    pool = create_pool(settings)
    await open_pool(pool)
    app.state.db_pool = pool
    app.state.database_check = SingleFlight(partial(health_repository.ping, pool))
    checkpointer_pool = create_checkpointer_pool(settings)
    await checkpointer_pool.open(wait=False)
    checkpointer = AsyncPostgresSaver(checkpointer_pool)
    server_started_at = clock.now()
    progress = RunProgress(pool, settings)
    workflow = build_workflow(app, progress, checkpointer)
    runner = AgentRunner(workflow, progress)
    register_finish_steps(app, progress, runner, workflow)
    app.state.run_progress = progress
    app.state.agent_runner = runner
    app.state.agent_workflow = workflow
    jobs = JobScheduler(progress, runner, server_started_at, app.state.workspace_manager,
                       app.state.run_cleanup)
    app.state.jobs = jobs
    try:
        if not await app.state.docker_check():
            logger.warning("Docker is not reachable: test runs will fail until it is started")
        else:
            await app.state.test_runner.remove_leftovers()  # containers left over after a crash (Gaps #7)
        await jobs.try_recovery()  # if the database is down now, the jobs try again in the background
        jobs.start()
        # Everything loaded at startup (modules, LangGraph, settings) lives until shutdown: moving it out of the
        # garbage collector's reach keeps full collections short (one took about 100 ms during 20 runs, B9-L02).
        gc.freeze()
        logger.info("SANGRA_AI started (mode=%s)", settings.app_mode)
        yield
    finally:
        await jobs.stop()
        await runner.shutdown()
        await app.state.background.drain()
        await checkpointer_pool.close()
        await close_pool(pool)
        logger.info("SANGRA_AI stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="SANGRA_AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.mailer = build_mailer(settings)
    app.state.background = BackgroundTasks()
    app.state.run_cleanup = RunCleanup(settings.packages_dir)
    app.state.workspace_manager = WorkspaceManager(settings)
    app.state.test_runner = TestRunner(settings)
    app.state.rate_limiter = RateLimiter()
    app.state.rate_rules = auth_rules(settings)
    app.state.dummy_hash = DummyHash(settings.bcrypt_rounds)
    app.state.docker_check = SingleFlight(partial(docker_reachable, settings.docker_check_timeout_seconds))

    install_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(projects.router, prefix=API_PREFIX)
    app.include_router(runs.router, prefix=API_PREFIX)

    # Added inner to outer: body limit, then request id / error catcher, then CORS (outermost,
    # so even 413 and 500 answers carry the CORS headers).
    app.add_middleware(
        BodySizeLimitMiddleware,
        # The upload route gets the zip limit plus 1 MB for the form around it (the zip itself is checked
        # against MAX_UPLOAD_BYTES by the controller).
        limit_for=upload_aware_limit(settings.max_body_bytes, settings.max_upload_bytes + UPLOAD_FORM_OVERHEAD),
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
