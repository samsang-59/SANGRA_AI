"""Cleanup that runs AFTER the database commit of a delete (project or run).

The database rows are already gone; this removes what lives outside the database: the package files and
the LangGraph data of each run (LangGraph's tables have no foreign key to ours). A failure here never undoes
the delete: it is logged, and the cleanup jobs (B10) remove leftovers later.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger("app.cleanup")

ThreadDeleter = Callable[[int], Awaitable[None]]


class RunCleanup:
    def __init__(self, packages_dir: Path):
        self.packages_dir = packages_dir.resolve()
        # Filled by the agent layer (B9) with "delete the LangGraph data of this run".
        self.thread_deleters: list[ThreadDeleter] = []

    async def after_runs_deleted(self, run_ids: list[int], package_paths: list[str]) -> None:
        for path in package_paths:
            try:
                await asyncio.to_thread(self.delete_package_file, path)
            except Exception as exc:
                logger.error("Could not delete a package file after a delete (%s); the cleanup job will retry",
                             type(exc).__name__)
        for run_id in run_ids:
            for deleter in self.thread_deleters:
                try:
                    await deleter(run_id)
                except Exception as exc:
                    logger.error("Could not delete the LangGraph data of run %s (%s)", run_id, type(exc).__name__)

    def delete_package_file(self, path: str) -> None:
        target = Path(path).resolve()
        if not target.is_relative_to(self.packages_dir):
            # Never delete anything outside the packages folder, whatever the database says.
            raise PermissionError("package path outside the packages folder")
        target.unlink(missing_ok=True)

    def delete_orphan_files(self, live_paths: set[str], min_age_seconds: float, now: float | None = None) -> int:
        """Deletes package files (and stale temporary files) in the packages folder that no live row points to and
        that are at least `min_age_seconds` old. Returns how many were deleted; a failure is skipped (next time)."""
        now = time.time() if now is None else now
        live = {Path(p).resolve() for p in live_paths}
        removed = 0
        if not self.packages_dir.is_dir():
            return removed
        for path in self.packages_dir.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.resolve() in live:
                    continue
                if path.stat().st_mtime < now - min_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed
