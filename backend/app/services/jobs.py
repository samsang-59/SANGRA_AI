"""Background jobs (04_backend_design.md, Layer 4 "Background jobs"). Safe to run twice: they only end runs
through the shared finish function, whose conditional update lets the first caller win.

- Startup recovery: runs left in started/searching/applying/testing are marked failed (server_restarted);
  runs waiting for approval or a hint are left alone (their state is saved and can be resumed).
- Timeout sweeper: every few minutes, runs waiting more than 1 hour are stopped (timed_out).
Package, workspace and token cleanup come with phase B10.
"""

import asyncio
import logging
from datetime import datetime

from app.agent.runner import AgentRunner
from app.repositories import run_repository
from app.services.run_progress import RunProgress

logger = logging.getLogger("app.jobs")


async def recover_interrupted_runs(progress: RunProgress, runner: AgentRunner, server_started_at: datetime) -> int:
    """Ends the runs that were working when the server stopped. Runs this process started are never touched."""
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


class JobScheduler:
    """Runs the jobs in the background until shutdown. A job that fails is logged and tried again next time."""

    RECOVERY_RETRY_SECONDS = 5.0

    def __init__(self, progress: RunProgress, runner: AgentRunner, server_started_at: datetime):
        self.progress = progress
        self.runner = runner
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
        self._tasks = [asyncio.create_task(self._timeout_loop(), name="job-timeout-sweeper")]
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

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
