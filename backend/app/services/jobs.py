"""Background jobs (04_backend_design.md, Layer 4 "Background jobs"). Safe to run twice: they only end runs
through the shared finish function, whose conditional update lets the first caller win.

- Startup recovery: runs left in started/searching/applying/testing are marked failed (server_restarted);
  runs waiting for approval or a hint are left alone (their state is saved and can be resumed).
- Timeout sweeper: every few minutes, runs waiting more than 1 hour are stopped (timed_out).
- Cleanup (every CLEANUP_INTERVAL_SECONDS): expired packages (file deleted, then deleted_at set), temporary copies older
  than WORKSPACE_MAX_AGE_HOURS (workspace_deleted_at set), files and folders no row points to any more (left behind by
  a delete whose file cleanup failed), and old email tokens. Work is done in batches; a file that cannot be deleted is
  logged and tried again next time.
"""

import asyncio
import logging
from datetime import datetime

from pathlib import Path

from app.agent.runner import AgentRunner
from app.repositories import package_repository, run_repository, user_repository
from app.runners.workspace_manager import WorkspaceManager
from app.services.cleanup_service import RunCleanup
from app.services.run_progress import RunProgress

logger = logging.getLogger("app.jobs")


async def recover_interrupted_runs(progress: RunProgress, runner: AgentRunner, server_started_at: datetime) -> int:
    """Ends the runs that were working when the server stopped (runs this process started are never touched), and
    completes the finish steps of runs that ended while the server stopped in the middle of finishing."""
    for run in await run_repository.find_unfinished_cleanups(progress.pool):
        if not runner.is_running(run["id"]):
            await progress.complete_finish(run)
    count = 0
    for run in await run_repository.find_interrupted_runs(progress.pool, server_started_at):
        if runner.is_running(run["id"]):
            continue
        if await progress.finish(run["id"], "failed", "server_restarted"):
            count += 1
    if count:
        logger.info("Startup recovery: %s interrupted runs marked failed (server_restarted)", count)
    return count


async def sweep_timeouts(progress: RunProgress, runner: AgentRunner) -> int:
    """Stops runs that waited for approval or a hint longer than the limit."""
    count = 0
    for run in await run_repository.find_stuck_waiting(progress.pool, progress.settings.waiting_timeout_seconds):
        if await progress.finish(run["id"], "stopped", "timed_out"):
            await runner.cancel(run["id"])  # normally nothing runs while a run waits
            count += 1
    if count:
        logger.info("Timeout sweeper: %s waiting runs stopped (timed_out)", count)
    return count


# A package file no row points to is only removed when it is at least this old (a package being built right now has
# its file written a moment before its row).
ORPHAN_PACKAGE_MIN_AGE_SECONDS = 3600.0


async def clean_expired_packages(progress: RunProgress, cleanup: RunCleanup) -> int:
    """Deletes the files of expired packages, batch by batch, and sets deleted_at. A file that cannot be deleted
    keeps its row unmarked, so it is tried again next time."""
    count = 0
    failed: set[int] = set()
    batch_size = progress.settings.cleanup_batch_size
    while True:
        batch = [p for p in await package_repository.find_expired(progress.pool, batch_size + len(failed))
                 if p["id"] not in failed][:batch_size]
        if not batch:
            break
        deleted, problems = await asyncio.to_thread(_delete_package_files, cleanup, batch)
        for package_id, error in problems:
            failed.add(package_id)
            logger.error("Package cleanup: the file of package %s could not be deleted (%s); tried again next time",
                         package_id, error)
        if deleted:
            count += await package_repository.mark_deleted(progress.pool, deleted)
        await asyncio.sleep(0)  # let other work run between batches
    if count:
        logger.info("Package cleanup: %s expired packages deleted", count)
    return count


def _delete_package_files(cleanup: RunCleanup, batch: list[dict]) -> tuple[list[int], list[tuple[int, str]]]:
    deleted, problems = [], []
    for package in batch:
        try:
            cleanup.delete_package_file(package["storage_path"])
            deleted.append(package["id"])
        except Exception as exc:
            problems.append((package["id"], type(exc).__name__))
    return deleted, problems


async def clean_old_workspaces(progress: RunProgress, workspaces: WorkspaceManager) -> int:
    """Deletes the temporary copies of finished runs that are older than the limit, then any run folder older than the
    limit that belongs to no active run (left behind by a deleted run)."""
    max_age = progress.settings.workspace_max_age_hours * 3600
    count = 0
    failed: set[int] = set()
    batch_size = progress.settings.cleanup_batch_size
    while True:
        batch = [r for r in await run_repository.find_old_workspaces(progress.pool, max_age, batch_size + len(failed))
                 if r["id"] not in failed]
        if not batch:
            break
        for run in batch[:batch_size]:
            try:
                await asyncio.to_thread(workspaces.delete, Path(run["workspace_path"]))
            except Exception as exc:
                failed.add(run["id"])
                logger.error("Workspace cleanup: the copy of run %s could not be deleted (%s); tried again next time",
                             run["id"], type(exc).__name__)
                continue
            await run_repository.mark_workspace_deleted(progress.pool, run["id"])
            count += 1
        await asyncio.sleep(0)
    keep = await run_repository.active_run_ids(progress.pool)
    orphans = await asyncio.to_thread(workspaces.delete_older_than, max_age, None, keep)
    if count or orphans:
        logger.info("Workspace cleanup: %s old copies and %s leftover folders deleted", count, len(orphans))
    return count + len(orphans)


async def clean_orphan_packages(progress: RunProgress, cleanup: RunCleanup) -> int:
    """Deletes package files that no live package row points to (a delete whose file cleanup failed)."""
    live = await package_repository.live_storage_paths(progress.pool)
    removed = await asyncio.to_thread(cleanup.delete_orphan_files, live, ORPHAN_PACKAGE_MIN_AGE_SECONDS)
    if removed:
        logger.info("Package cleanup: %s leftover package files deleted", removed)
    return removed


async def clean_old_tokens(progress: RunProgress) -> int:
    count = await user_repository.delete_old_tokens(progress.pool, progress.settings.token_retention_hours)
    if count:
        logger.info("Token cleanup: %s old email tokens deleted", count)
    return count


async def run_cleanup_jobs(progress: RunProgress, workspaces: WorkspaceManager, cleanup: RunCleanup) -> dict:
    """Every cleanup job once. One failing job does not stop the others."""
    results: dict[str, int | None] = {}
    jobs = {
        "packages": lambda: clean_expired_packages(progress, cleanup),
        "workspaces": lambda: clean_old_workspaces(progress, workspaces),
        "orphan_packages": lambda: clean_orphan_packages(progress, cleanup),
        "tokens": lambda: clean_old_tokens(progress),
    }
    for name, job in jobs.items():
        try:
            results[name] = await job()
        except Exception as exc:
            results[name] = None
            logger.error("Cleanup job %s failed (%s); it will run again", name, type(exc).__name__)
    return results


class JobScheduler:
    """Runs the jobs in the background until shutdown. A job that fails is logged and tried again next time."""

    RECOVERY_RETRY_SECONDS = 5.0

    def __init__(self, progress: RunProgress, runner: AgentRunner, server_started_at: datetime,
                 workspaces: WorkspaceManager, cleanup: RunCleanup):
        self.progress = progress
        self.runner = runner
        self.workspaces = workspaces
        self.cleanup = cleanup
        self.server_started_at = server_started_at
        self.recovered = False
        self._tasks: list[asyncio.Task] = []

    async def try_recovery(self) -> None:
        try:
            await recover_interrupted_runs(self.progress, self.runner, self.server_started_at)
            self.recovered = True
        except Exception as exc:
            logger.error("Startup recovery failed (%s); it will be tried again", type(exc).__name__)

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._timeout_loop(), name="job-timeout-sweeper"),
                       asyncio.create_task(self._cleanup_loop(), name="job-cleanup")]
        if not self.recovered:
            self._tasks.append(asyncio.create_task(self._recovery_loop(), name="job-startup-recovery"))

    async def _recovery_loop(self) -> None:
        while not self.recovered:
            await asyncio.sleep(self.RECOVERY_RETRY_SECONDS)
            await self.try_recovery()

    async def _timeout_loop(self) -> None:
        while True:
            await asyncio.sleep(self.progress.settings.timeout_sweep_interval_seconds)
            try:
                await sweep_timeouts(self.progress, self.runner)
            except Exception as exc:
                logger.error("Timeout sweeper failed (%s); it will run again", type(exc).__name__)

    async def run_cleanup(self) -> dict:
        return await run_cleanup_jobs(self.progress, self.workspaces, self.cleanup)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.progress.settings.cleanup_interval_seconds)
            await self.run_cleanup()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
