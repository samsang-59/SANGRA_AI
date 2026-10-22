"""Shares one in-flight check between callers that ask at the same moment.

Used by /health: 500 parallel health requests cause one database ping and one Docker ping, not 500.
Nothing is cached: a caller that arrives after a check finished starts a fresh one.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class SingleFlight(Generic[T]):
    def __init__(self, func: Callable[[], Awaitable[T]]):
        self._func = func
        self._task: asyncio.Task[T] | None = None

    async def __call__(self) -> T:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._func())
        # shield: a caller that gives up (client disconnect) does not cancel the check for the others.
        return await asyncio.shield(self._task)
