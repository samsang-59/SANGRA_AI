"""Small fire-and-forget tasks owned by the app (for example sending an email after the answer is sent).

Tasks are kept in a set so they are not garbage collected mid-way, their errors are logged, and on
shutdown the app waits for them (with a time limit) so nothing is cut off silently.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger("app.background")


class BackgroundTasks:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background task %s failed (%s)", task.get_name(), type(task.exception()).__name__)

    async def drain(self, timeout: float = 10.0) -> None:
        """Waits for the running tasks; cancels whatever is still running after the timeout."""
        if not self._tasks:
            return
        pending = list(self._tasks)
        _, still_running = await asyncio.wait(pending, timeout=timeout)
        for task in still_running:
            task.cancel()
        if still_running:
            await asyncio.gather(*still_running, return_exceptions=True)
