"""Agent-side helpers (Layer 4): what the agent's steps use to report progress. The agent never touches
the database itself; it calls these, and these call the repositories.

finish() is the ONE shared "finalize" function: the agent's own finish step, a cancel and the 1-hour
timeout all end runs through it. Only the first caller wins (a conditional update); the finish hooks
(package, workspace deletion, LangGraph snapshot deletion; filled in by later phases) run once, for the winner.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.repositories import attempt_repository, event_repository, run_repository

logger = logging.getLogger("app.run")

FinishHook = Callable[[dict], Awaitable[None]]

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


def summary_for(final_status: str, stop_reason: str | None) -> str:
    """The final summary, from a template (no LLM call)."""
    if final_status == "succeeded":
        return "The fix was applied and the tests passed."
    reason = STOP_REASON_TEXT.get(stop_reason or "", "The run ended.")
    return f"The run {final_status}. {reason}"


@dataclass
class RunProgress:
    pool: AsyncConnectionPool
    settings: Settings
    finish_hooks: list[FinishHook] = field(default_factory=list)

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

    async def mark_finished(self, run_id: int, final_status: str, stop_reason: str | None,
                            summary: str | None = None) -> dict | None:
        """Step 1 of finishing: the final status (conditional). None = the run had already finished."""
        run = await run_repository.finish_run(self.pool, run_id, final_status, stop_reason,
                                              summary or summary_for(final_status, stop_reason))
        if run is not None:
            logger.info("Run %s finished: %s (%s)", run_id, final_status, stop_reason)
        return run

    async def run_finish_hooks(self, run: dict) -> None:
        """Step 2 of finishing. A failing hook is logged and never stops the others."""
        for hook in self.finish_hooks:
            try:
                await hook(run)
            except Exception as exc:
                logger.error("A finish step failed for run %s (%s)", run["id"], type(exc).__name__)

    async def finish(self, run_id: int, final_status: str, stop_reason: str | None,
                     summary: str | None = None) -> bool:
        """The shared finalize function. True if this call ended the run; False if it was already finished."""
        run = await self.mark_finished(run_id, final_status, stop_reason, summary)
        if run is None:
            return False
        await self.run_finish_hooks(run)
        return True
