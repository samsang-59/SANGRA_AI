"""FakeWorkflow: plays the agent with scripted steps and delays (phase B5). It reports progress only through
RunProgress, exactly like the real agent will, and reads decisions from the database (not from the resume value).

Script options:
- tool_events: how many tool_called events the search makes
- delay: seconds to wait between steps
- need_hint: ask for a hint before proposing (once per run)
- crash: raise an unexpected error at the start
- pause_forever: stop in "searching" and wait (to test cancel and restarts)
- passive: do nothing at all on start and resume (the test drives the run itself)
"""

import asyncio
from typing import Any

from app.services.run_progress import RunProgress

EDIT = {"file_path": "src/main/java/shop/Discount.java", "start_line": 3, "end_line": 3,
        "old_code": "    double rate = 0.05; // SECRET_OLD_CODE", "new_code": "    double rate = 0.10; // SECRET_NEW_CODE",
        "explanation": "The summer sale rate"}


class FakeWorkflow:
    def __init__(self, progress: RunProgress, *, tool_events: int = 3, delay: float = 0.0, need_hint: bool = False,
                 crash: bool = False, pause_forever: bool = False, passive: bool = False, test_seconds: float = 0.0):
        self.progress = progress
        self.tool_events = tool_events
        self.delay = delay
        self.need_hint = need_hint
        self.crash = crash
        self.pause_forever = pause_forever
        self.passive = passive
        self.test_seconds = test_seconds
        self.starts: list[int] = []
        self.resumes: list[tuple[int, Any]] = []
        self.cancelled: list[int] = []
        self.hinted: set[int] = set()
        self.active = 0
        self.max_active = 0

    async def delete_snapshots(self, run_id: int) -> None:
        """The fake keeps no LangGraph snapshots."""

    async def _step(self) -> None:
        await asyncio.sleep(self.delay)

    async def start(self, run_id: int) -> None:
        self.starts.append(run_id)
        if self.passive:
            return
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self._step()
            if self.crash:
                raise RuntimeError("the fake agent crashed")
            await self.progress.change_status(run_id, ("started",), "searching")
            if self.pause_forever:
                await asyncio.sleep(3600)
            await self._search(run_id)
            if self.need_hint and run_id not in self.hinted:
                await self._ask_for_hint(run_id)
                return
            await self._propose(run_id, "first")
        except asyncio.CancelledError:
            self.cancelled.append(run_id)
            raise
        finally:
            self.active -= 1

    async def _search(self, run_id: int) -> None:
        for n in range(self.tool_events):
            await self.progress.emit(run_id, "tool_called", f"search_files('discount') #{n + 1}",
                                     {"tool": "search_files", "files": 2})
            await self._step()

    async def _ask_for_hint(self, run_id: int) -> None:
        outcome = await self.progress.request_hint(run_id)
        if outcome["outcome"] == "limit_reached":
            await self.progress.finish(run_id, "failed", "no_match_after_3_hints")
        self.hinted.add(run_id)

    async def _propose(self, run_id: int, reason: str) -> None:
        attempt = await self.progress.create_attempt(run_id, reason, "The summer rate should be 10%.", [EDIT])
        await self.progress.request_approval(run_id, attempt["id"])

    async def resume(self, run_id: int, value: Any) -> None:
        self.resumes.append((run_id, value))
        if self.passive:
            return
        try:
            run = await self.progress.load_run(run_id)
            if run is None:
                return
            if run["status"] == "applying":  # accepted (read from the database)
                await self._step()
                await self.progress.change_status(run_id, ("applying",), "testing")
                await asyncio.sleep(self.test_seconds)
                await self.progress.finish(run_id, "succeeded", None)
            elif run["status"] == "searching" and isinstance(value, dict) and "hint" in value:
                await self._search(run_id)
                await self._propose(run_id, "first")
            elif run["status"] == "searching":  # rejected
                if run["reject_count"] >= self.progress.settings.max_rejects:
                    await self.progress.finish(run_id, "stopped", "reject_limit_reached")
                else:
                    await self._search(run_id)
                    await self._propose(run_id, "after_reject")
        except asyncio.CancelledError:
            self.cancelled.append(run_id)
            raise
