"""Agent-side helpers (Layer 4): what the agent's steps use to report progress. The agent never touches
the database itself; it calls these, and these call the repositories.

finish() is the ONE shared "finalize" function: the agent's own finish step, a cancel and the 1-hour
timeout all end runs through it. Only the first caller wins (a conditional update); the finish hooks
(package, workspace deletion, LangGraph snapshot deletion; filled in by later phases) run once, for the winner.
"""

import asyncio
import logging
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.repositories import attempt_repository, event_repository, run_repository, test_result_repository
from app.repositories.event_repository import FINAL_STATUSES

logger = logging.getLogger("app.run")

FinishHook = Callable[[dict], Awaitable[None]]
BeforeFinalHook = Callable[[dict, str, "str | None", str], Awaitable[None]]

# Plain words for every stop reason in the design (04_backend_design.md, step 11).
STOP_REASON_TEXT = {
    "cancelled_by_user": "You cancelled the run.",
    "timed_out": "The run waited more than 1 hour for your answer, so it was stopped.",
    "reject_limit_reached": "The fix was rejected 3 times.",
    "tests_failed_3_times": "The tests failed after 3 tries.",
    "proposal_invalid": "The agent could not produce a valid fix 3 times in a row.",
    "no_match_after_3_hints": "The agent could not find the bug, even after 3 hints.",
    "clone_failed": "The code could not be copied (clone, folder or zip problem).",
    "test_runner_error": "The tests could not be started (Docker problem).",
    "apply_failed": "The approved changes could not be applied.",
    "undo_failed": "A failed fix could not be undone cleanly.",
    "llm_error": "The AI service did not answer.",
    "internal_error": "An unexpected error stopped the run.",
    "server_restarted": "The server restarted while the run was working.",
}


NO_TEST_COVERS_LABEL = "Tests passed, but no test covers this bug, please verify the fix yourself."


def summary_for(final_status: str, stop_reason: str | None, baseline_passed: bool = False) -> str:
    """The final summary, from a template (no LLM call)."""
    if final_status == "succeeded":
        if baseline_passed:  # the tests already passed before the fix, so they prove nothing about it
            return f"The fix was applied. {NO_TEST_COVERS_LABEL}"
        return "The fix was applied and the tests passed."
    reason = STOP_REASON_TEXT.get(stop_reason or "", "The run ended.")
    return f"The run {final_status}. {reason}"


@dataclass
class RunProgress:
    pool: AsyncConnectionPool
    settings: Settings
    # Steps BEFORE the final status is saved (undo, build the package), called with
    # (run, final_status, stop_reason, summary).
    before_final_hooks: list[BeforeFinalHook] = field(default_factory=list)
    # Steps AFTER the final status is saved (delete the temporary copy, delete the LangGraph snapshot).
    finish_hooks: list[FinishHook] = field(default_factory=list)
    # Stops the run's background agent task (set at startup to AgentRunner.cancel); used by cancel().
    stop_agent: Callable[[int], Awaitable[None]] | None = None
    _locks: "weakref.WeakValueDictionary[int, asyncio.Lock]" = field(default_factory=weakref.WeakValueDictionary)

    def _lock(self, run_id: int) -> asyncio.Lock:
        """One finish at a time per run (a cancel and the agent's own finish at the same moment: one package, one
        final status). One backend process, so an in-memory lock is enough (as for the rate limiter)."""
        lock = self._locks.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[run_id] = lock
        return lock

    async def emit(self, run_id: int, event_type: str, message: str, details: dict | None = None,
                   attempt_id: int | None = None) -> dict | None:
        """One line in the run's diary. Details must stay SHORT (names, counts, line ranges), never code."""
        return await event_repository.add_event(self.pool, run_id, attempt_id, event_type, message, details)

    async def change_status(self, run_id: int, allowed_from: tuple[str, ...], to_status: str) -> dict | None:
        return await run_repository.transition_status(self.pool, run_id, allowed_from, to_status)

    async def request_hint(self, run_id: int) -> dict:
        return await run_repository.request_hint(self.pool, run_id, self.settings.max_hints)

    async def create_attempt(self, run_id: int, reason: str, explanation: str, edits: list[dict]) -> dict:
        return await attempt_repository.create_attempt_with_edits(self.pool, run_id, reason, explanation, edits)

    async def request_approval(self, run_id: int, attempt_id: int) -> dict | None:
        return await attempt_repository.request_approval(self.pool, run_id, attempt_id)

    async def increment(self, run_id: int, counter: str) -> int | None:
        return await run_repository.increment_counter(self.pool, run_id, counter)

    async def load_run(self, run_id: int) -> dict | None:
        return await run_repository.find_by_id(self.pool, run_id)

    async def set_workspace_ready(self, run_id: int, workspace_path: str, source_commit: str | None) -> None:
        await run_repository.set_workspace_ready(self.pool, run_id, workspace_path, source_commit)

    async def add_tokens(self, run_id: int, tokens: int) -> None:
        if tokens:
            await run_repository.add_tokens(self.pool, run_id, tokens)

    async def mark_workspace_deleted(self, run_id: int) -> None:
        await run_repository.mark_workspace_deleted(self.pool, run_id)

    async def save_test_result(self, run_id: int, attempt_id: int | None, test_type: str, outcome) -> dict:
        return await test_result_repository.create(
            self.pool, run_id, attempt_id, test_type, outcome.status, outcome.exit_code, outcome.log_excerpt,
            outcome.duration_ms, outcome.docker_image, outcome.started_at, outcome.finished_at)

    async def attempt_with_edits(self, run_id: int, attempt_id: int) -> dict | None:
        return await attempt_repository.find_with_edits(self.pool, run_id, attempt_id)

    async def attempts_of_run(self, run_id: int) -> list[dict]:
        return await attempt_repository.list_with_edits(self.pool, run_id)

    async def mark_attempt(self, attempt_id: int, status: str) -> None:
        await attempt_repository.mark_attempt_status(self.pool, attempt_id, status)

    async def set_reverted(self, attempt_id: int) -> None:
        await attempt_repository.set_reverted_at(self.pool, attempt_id)

    async def latest_hint(self, run_id: int) -> str | None:
        return await event_repository.latest_hint(self.pool, run_id)

    async def mark_finished(self, run_id: int, final_status: str, stop_reason: str | None,
                            summary: str | None = None) -> dict | None:
        """Step 1 of finishing: the final status (conditional). None = the run had already finished."""
        if summary is None:
            baseline = await test_result_repository.find_baseline(self.pool, run_id)
            summary = summary_for(final_status, stop_reason, bool(baseline and baseline["status"] == "passed"))
        run = await run_repository.finish_run(self.pool, run_id, final_status, stop_reason, summary)
        if run is not None:
            logger.info("Run %s finished: %s (%s)", run_id, final_status, stop_reason)
        return run

    async def _summary(self, run_id: int, final_status: str, stop_reason: str | None) -> str:
        baseline = await test_result_repository.find_baseline(self.pool, run_id)
        return summary_for(final_status, stop_reason, bool(baseline and baseline["status"] == "passed"))

    async def _run_before_final(self, run: dict, final_status: str, stop_reason: str | None, summary: str) -> None:
        for hook in self.before_final_hooks:
            try:
                await hook(run, final_status, stop_reason, summary)
            except Exception as exc:
                logger.error("A finish step failed for run %s (%s)", run["id"], type(exc).__name__)

    async def run_finish_hooks(self, run: dict) -> None:
        """The steps after the final status. A failing step is logged and never stops the others."""
        for hook in self.finish_hooks:
            try:
                await hook(run)
            except Exception as exc:
                logger.error("A finish step failed for run %s (%s)", run["id"], type(exc).__name__)

    async def finish(self, run_id: int, final_status: str, stop_reason: str | None,
                     summary: str | None = None) -> bool:
        """The shared finalize function (04_backend_design.md 6.4 step 11), in its order: undo and build the package,
        then save the final status (its status_changed event is the last event), then delete the temporary copy.
        True if this call ended the run; False if it was already finished."""
        async with self._lock(run_id):
            run = await run_repository.find_by_id(self.pool, run_id)
            if run is None or run["status"] in FINAL_STATUSES:
                return False
            summary = summary or await self._summary(run_id, final_status, stop_reason)
            await self._run_before_final(run, final_status, stop_reason, summary)
            finished = await self.mark_finished(run_id, final_status, stop_reason, summary)
            if finished is None:
                return False
            await self.run_finish_hooks(finished)
            return True

    async def cancel(self, run_id: int) -> dict | None:
        """Cancel (04_backend_design.md RunService.cancel_run), in its order: mark the run stopped (conditional: of a
        cancel and an Accept at the same moment only one wins), stop the agent and its test container, undo the
        applied edits, build the package, delete the copy. None if the run was not active any more."""
        async with self._lock(run_id):
            summary = await self._summary(run_id, "stopped", "cancelled_by_user")
            finished = await self.mark_finished(run_id, "stopped", "cancelled_by_user", summary)
            if finished is None:
                return None
            if self.stop_agent is not None:
                await self.stop_agent(run_id)
            await self._run_before_final(finished, "stopped", "cancelled_by_user", summary)
            await self.run_finish_hooks(finished)
            return finished

    async def complete_finish(self, run: dict) -> None:
        """Startup recovery: a finished run whose finish steps were cut off (the server stopped in the middle). The
        package is built if it is missing, then the copy is deleted."""
        async with self._lock(run["id"]):
            await self._run_before_final(run, run["status"], run["stop_reason"], run["final_summary"] or "")
            await self.run_finish_hooks(run)
