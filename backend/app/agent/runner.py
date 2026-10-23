"""AgentRunner (04_backend_design.md 6.5): one background task per run, kept in memory.

start(run_id) starts the workflow in the background; resume(run_id, value) continues a paused one (called
by services after an approval or a hint); cancel(run_id) stops the running task. After the workflow ends
(normally, by an unexpected error, or by cancel) the runner finalizes the run on errors, calls the
after-end hooks (B9: delete the LangGraph snapshots) and forgets the task.

The workflow is given from outside: the LangGraph graph (app/agent/workflow.py); tests can plug in another one.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.core.logging import run_id_var
from app.services.run_progress import RunProgress

logger = logging.getLogger("app.agent")

AfterEndHook = Callable[[int], Awaitable[None]]


class Workflow(Protocol):
    async def start(self, run_id: int) -> None: ...

    async def resume(self, run_id: int, value: Any) -> None: ...


class AgentRunner:
    def __init__(self, workflow: Workflow, progress: RunProgress):
        self.workflow = workflow
        self.progress = progress
        self.after_end_hooks: list[AfterEndHook] = []
        self._tasks: dict[int, asyncio.Task] = {}

    def is_running(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def running_count(self) -> int:
        return sum(1 for task in self._tasks.values() if not task.done())

    def start(self, run_id: int) -> bool:
        return self._spawn(run_id, lambda: self.workflow.start(run_id), "start")

    def resume(self, run_id: int, value: Any = None) -> bool:
        """Continues a paused run. Ignored (False) if a task for this run is already running."""
        return self._spawn(run_id, lambda: self.workflow.resume(run_id, value), "resume")

    def _spawn(self, run_id: int, make: Callable[[], Awaitable[None]], kind: str) -> bool:
        if self.is_running(run_id):
            logger.warning("Run %s already has a running task; %s ignored", run_id, kind)
            return False
        self._tasks[run_id] = asyncio.create_task(self._drive(run_id, make), name=f"agent-{kind}-{run_id}")
        return True

    async def _drive(self, run_id: int, make: Callable[[], Awaitable[None]]) -> None:
        run_id_var.set(str(run_id))
        try:
            await make()
        except asyncio.CancelledError:
            logger.info("Run %s: agent task stopped", run_id)
            raise
        except Exception:
            logger.exception("Run %s: the agent failed with an unexpected error", run_id)
            # The run must never stay active forever: end it as failed (does nothing if it already ended).
            await self._finish_after_error(run_id)
        finally:
            # The after-end steps (deleting the LangGraph snapshot of a finished run) run BEFORE the task is
            # forgotten, so nobody sees the run as idle while its snapshot still exists.
            for hook in self.after_end_hooks:
                try:
                    await hook(run_id)
                except Exception as exc:
                    logger.error("Run %s: an after-end step failed (%s)", run_id, type(exc).__name__)
            if self._tasks.get(run_id) is asyncio.current_task():
                del self._tasks[run_id]

    FINISH_RETRY_FIRST_SECONDS = 1.0
    FINISH_RETRY_MAX_SECONDS = 30.0

    async def _finish_after_error(self, run_id: int) -> None:
        """Ends the run as failed. When that cannot be saved either (the database is down: the usual reason the
        agent failed), it is tried again with growing pauses until the database is back (B11-R01). A server stop in
        the meantime cancels this; startup recovery then ends the run."""
        delay = self.FINISH_RETRY_FIRST_SECONDS
        while True:
            try:
                await self.progress.finish(run_id, "failed", "internal_error")
                return
            except Exception as exc:
                logger.error("Run %s: could not finalize after the error (%s); trying again in %.0f s",
                             run_id, type(exc).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.FINISH_RETRY_MAX_SECONDS)

    async def cancel(self, run_id: int, timeout: float = 10.0) -> None:
        """Stops the running task of a run (if any) and waits for it to end."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.wait({task}, timeout=timeout)

    async def shutdown(self) -> None:
        """Server shutdown: stop every running task. Their runs are recovered at the next start."""
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)
