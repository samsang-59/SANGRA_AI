"""Building the download package of a finished run (the package step of the finish, 04_backend_design.md 6.4 step 11).

At most one package per run: when the run already has one (for example the server stopped after building it but
before saving the final status), nothing new is built. If building fails the run still finishes; the user still sees
the changes in the run's records, and an error event says the package is missing.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.repositories import attempt_repository, event_repository, package_repository, test_result_repository
from app.runners.finalizer import PackageInput, build_package
from app.runners.workspace_manager import WorkspaceManager

logger = logging.getLogger("app.package")

# Packages are built by a small pool of their own, so many runs finishing at once do not fill the default thread pool
# (used by every other file task) with zipping work. Extra packages wait their turn.
_PACKAGE_THREADS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="package")


def package_path(settings: Settings, user_id: int, run_id: int) -> Path:
    return settings.packages_dir / str(int(user_id)) / f"run_{int(run_id)}.zip"


async def build_run_package(pool: AsyncConnectionPool, settings: Settings, workspaces: WorkspaceManager, run: dict,
                            final_status: str, stop_reason: str | None, summary: str) -> dict | None:
    if await package_repository.find_by_run(pool, run["id"]) is not None:
        return None  # never a second package
    run_dir = Path(run["workspace_path"]) if run.get("workspace_path") else None
    code_dir = run_dir / "code" if run_dir else None
    try:
        attempts = await attempt_repository.list_with_edits(pool, run["id"])
        results = await test_result_repository.list_by_run(pool, run["id"])
        patch = None
        if final_status == "succeeded" and code_dir is not None and (code_dir / ".git").exists():
            patch = await _in_package_thread(workspaces.diff, code_dir)
        data = PackageInput(run, final_status, stop_reason, summary, attempts, results, patch, code_dir,
                            run_dir / "artifacts" if run_dir else None)
        target = package_path(settings, run["user_id"], run["id"])
        size = await _in_package_thread(build_package, data, target)
        row = await package_repository.create(pool, run["id"], str(target), size, settings.package_expiry_days)
        if row is None:  # someone else saved one at the same moment
            target.unlink(missing_ok=True)
        return row
    except Exception as exc:
        logger.error("Run %s: the download package could not be built (%s)", run["id"], type(exc).__name__)
        await event_repository.add_event(pool, run["id"], None, "error",
                                         "The download package could not be built; the changes are still shown "
                                         "in the run.", {"package": "missing"})
        return None


async def _in_package_thread(func, *args):
    return await asyncio.get_running_loop().run_in_executor(_PACKAGE_THREADS, func, *args)
