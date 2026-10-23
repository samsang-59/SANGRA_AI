"""The 11 steps of the graph (04_backend_design.md 6.4). Each step returns a small update of the state and sets
`outcome`, the word that tells the graph which arrow to follow. The steps call services (RunProgress, the
WorkspaceManager, the TestRunner, the LLM functions); they never touch the database themselves.

Outcomes: "ok" (go on), "finish" (end the run: final_status and stop_reason are set), "end" (the run was already
ended by someone else, for example a cancel, so the graph just stops), plus the step-specific ones in graph.py.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from langgraph.types import interrupt

from app.agent.edits import Edit, EditError, bottom_up_order, plan_edits, write_planned
from app.agent.llm.client import ChatModel, LLMClient, LLMError
from app.agent.llm.investigation import InvestigationContext, investigate
from app.agent.llm.proposal import ProposalContext, propose_fix
from app.agent.state import AgentState
from app.agent.tools.code_tools import CodeTools
from app.core.config import Settings
from app.runners.workspace_manager import WorkspaceError, WorkspaceManager
from app.services.run_progress import RunProgress

logger = logging.getLogger("app.agent")

FINAL = ("succeeded", "failed", "stopped")
BASELINE_EXCERPT_CHARS = 1500
FAILURE_EXCERPT_CHARS = 3000


@dataclass
class AgentServices:
    progress: RunProgress
    workspaces: WorkspaceManager
    test_runner: object               # TestRunner, or a fake with the same run(...) method
    llm_model: Callable[[], ChatModel]  # builds the model for each step (Groq, or a fake in test setups)
    settings: Settings


def _finish(status: str, reason: str | None, **extra) -> dict:
    return {"outcome": "finish", "final_status": status, "stop_reason": reason, **extra}


class AgentSteps:
    def __init__(self, services: AgentServices):
        self.s = services

    # helpers -------------------------------------------------------------------------------------

    def _code_dir(self, state: AgentState) -> Path:
        return Path(state["workspace_path"]) / "code"

    def _artifacts(self, state: AgentState) -> Path:
        return Path(state["workspace_path"]) / "artifacts"

    def _llm(self) -> LLMClient:
        return LLMClient(self.s.llm_model(), self.s.settings)

    async def _error_event(self, run_id: int, message: str) -> None:
        await self.s.progress.emit(run_id, "error", message[:500])

    async def _run_tests(self, state: AgentState, attempt_id: int | None, label: str, log_name: str):
        progress = self.s.progress
        await progress.emit(state["run_id"], "tests_started", f"Running the tests ({label}).",
                            {"test_type": "baseline" if attempt_id is None else "after_fix"}, attempt_id)
        outcome = await self.s.test_runner.run(
            run_id=state["run_id"], user_id=state["user_id"], code_dir=self._code_dir(state),
            language=state["language"], test_command=state["test_command"],
            log_path=self._artifacts(state) / log_name)
        await progress.save_test_result(state["run_id"], attempt_id, "baseline" if attempt_id is None else "after_fix",
                                        outcome)
        await progress.emit(state["run_id"], "tests_finished", f"Tests {label}: {outcome.status}.",
                            {"status": outcome.status, "exit_code": outcome.exit_code,
                             "duration_ms": outcome.duration_ms}, attempt_id)
        return outcome

    # 1 ------------------------------------------------------------------------------------------

    async def prepare_workspace(self, state: AgentState) -> dict:
        run = await self.s.progress.load_run(state["run_id"])
        if run is None or run["status"] in FINAL:
            return {"outcome": "end"}
        try:
            workspace = await asyncio.to_thread(self.s.workspaces.prepare, run["user_id"], run["id"],
                                                run["source_type"], run["source_reference"], run["branch"])
        except WorkspaceError as exc:
            await self._error_event(run["id"], exc.message)
            return _finish("failed", exc.reason)
        await self.s.progress.set_workspace_ready(run["id"], str(workspace.run_dir), workspace.source_commit)
        return {"outcome": "ok", "workspace_path": str(workspace.run_dir)}

    # 2 ------------------------------------------------------------------------------------------

    async def run_baseline_tests(self, state: AgentState) -> dict:
        outcome = await self._run_tests(state, None, "before any change (baseline)", "baseline.log")
        if outcome.status in ("error", "timed_out"):
            docker_problem = outcome.status == "error" and outcome.exit_code is None
            await self._error_event(state["run_id"], f"Your tests could not run ({outcome.note}). Check the test "
                                                     "command." if not docker_problem else
                                    f"The tests could not be started ({outcome.note}).")
            return _finish("failed", "test_runner_error" if docker_problem else "baseline_could_not_run")
        summary = (f"{outcome.status} (exit code {outcome.exit_code}). End of the log:\n"
                   f"{outcome.log_excerpt[-BASELINE_EXCERPT_CHARS:]}")
        if await self.s.progress.change_status(state["run_id"], ("started",), "searching") is None:
            return {"outcome": "end"}
        return {"outcome": "ok", "baseline_summary": summary, "baseline_passed": outcome.status == "passed",
                "hints": [], "search_terms_tried": [], "findings": [], "proposal_checks_used": 0,
                "check_problems": [], "next_reason": "first"}

    # 3 ------------------------------------------------------------------------------------------

    async def investigate(self, state: AgentState) -> dict:
        run_id = state["run_id"]
        llm = self._llm()
        settings = self.s.settings
        tools = CodeTools(self._code_dir(state), max_files=settings.search_max_files, max_lines=settings.read_max_lines,
                          file_cap_bytes=settings.file_read_cap_bytes, output_max_chars=settings.tool_output_max_chars)

        async def on_tool(name: str, args: dict, summary: dict) -> None:
            shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
            await self.s.progress.emit(run_id, "tool_called", f"{name}({shown})"[:300], {"tool": name, **summary})

        context = InvestigationContext(state["bug_description"], state["language"], list(state.get("hints", [])),
                                       state.get("baseline_summary", ""), list(state.get("search_terms_tried", [])))
        try:
            result = await investigate(llm, tools, context, settings.investigate_max_tool_calls, on_tool)
        except LLMError as exc:
            await self.s.progress.add_tokens(run_id, llm.total_tokens)
            await self._error_event(run_id, str(exc))
            return _finish("failed", "llm_error")
        await self.s.progress.add_tokens(run_id, result.tokens)
        places = [{"file": f.file_path, "lines": f"{f.start_line}-{f.end_line}"} for f in result.findings]
        message = (f"Found {len(places)} likely place(s) of the bug." if result.outcome == "found"
                   else "The search did not find the place of the bug.")
        await self.s.progress.emit(run_id, "tool_result", message, {"outcome": result.outcome, "places": places})
        return {"outcome": result.outcome, "findings": [f.as_dict() for f in result.findings],
                "search_terms_tried": result.search_terms_tried, "investigate_steps_used": result.tool_calls_used}

    # 4 ------------------------------------------------------------------------------------------

    async def request_hint(self, state: AgentState) -> dict:
        outcome = await self.s.progress.request_hint(state["run_id"])
        if outcome["outcome"] == "limit_reached":
            await self._error_event(state["run_id"], "The bug could not be found, even after the hints.")
            return _finish("failed", "no_match_after_3_hints")
        if outcome["outcome"] != "ok":
            return {"outcome": "end"}
        return {"outcome": "wait"}

    async def wait_for_hint(self, state: AgentState) -> dict:
        # Only pauses: on resume LangGraph runs this step again from its first line, so nothing happens before.
        interrupt({"waiting_for": "hint"})
        run = await self.s.progress.load_run(state["run_id"])
        if run is None or run["status"] in FINAL:
            return {"outcome": "end"}
        if run["status"] != "searching":  # resumed without a saved hint: keep waiting
            return {"outcome": "wait"}
        hint = await self.s.progress.latest_hint(state["run_id"])  # read from the database, not the resume value
        return {"outcome": "ok", "hints": [*state.get("hints", []), hint or ""], "investigate_steps_used": 0}

    # 5 ------------------------------------------------------------------------------------------

    async def propose_fix(self, state: AgentState) -> dict:
        run_id = state["run_id"]
        settings = self.s.settings
        reason = state.get("next_reason", "first")
        attempts = await self.s.progress.attempts_of_run(run_id)
        llm = self._llm()
        context = ProposalContext(
            bug_description=state["bug_description"], language=state["language"],
            findings=list(state.get("findings", [])), hints=list(state.get("hints", [])),
            baseline_summary=state.get("baseline_summary", ""), previous_attempts=[_attempt_line(a) for a in attempts],
            reject_feedback=state.get("reject_feedback") if reason == "after_reject" else None,
            last_test_failure=state.get("last_test_failure") if reason == "after_test_failure" else None,
            check_problems=list(state.get("check_problems", [])),
            max_edits=settings.max_edits_per_attempt, max_changed_lines=settings.max_changed_lines)
        try:
            result = await propose_fix(llm, context)
        except LLMError as exc:
            await self.s.progress.add_tokens(run_id, llm.total_tokens)
            await self._error_event(run_id, str(exc))
            return _finish("failed", "llm_error")
        await self.s.progress.add_tokens(run_id, result.tokens)
        if not result.ok:
            return await self._failed_check(state, [result.error or "The fix could not be read."])
        await self.s.progress.emit(run_id, "tool_result", f"A fix was drafted ({len(result.edits)} edits); checking it.",
                                   {"edits": len(result.edits)})
        return {"outcome": "check", "draft_edits": [e.as_dict() for e in result.edits],
                "draft_explanation": result.explanation}

    async def _failed_check(self, state: AgentState, problems: list[str]) -> dict:
        used = state.get("proposal_checks_used", 0) + 1
        await self.s.progress.emit(state["run_id"], "error", f"The drafted fix did not pass the checks "
                                                            f"({len(problems)} problem(s)).", {"check": used})
        if used >= self.s.settings.max_proposal_checks:
            return _finish("failed", "proposal_invalid", proposal_checks_used=used)
        return {"outcome": "retry", "proposal_checks_used": used, "check_problems": problems[:10],
                "draft_edits": [], "draft_explanation": ""}

    # 6 ------------------------------------------------------------------------------------------

    async def check_proposal(self, state: AgentState) -> dict:
        settings = self.s.settings
        edits = [Edit.from_dict(e) for e in state.get("draft_edits", [])]
        try:
            await asyncio.to_thread(plan_edits, self._code_dir(state), edits, settings.max_edits_per_attempt,
                                    settings.max_changed_lines)
        except EditError as exc:
            return await self._failed_check(state, exc.problems)
        attempt = await self.s.progress.create_attempt(state["run_id"], state.get("next_reason", "first"),
                                                       state.get("draft_explanation", ""),
                                                       bottom_up_order(state.get("draft_edits", [])))
        return {"outcome": "ok", "current_attempt_id": attempt["id"], "draft_edits": [], "draft_explanation": "",
                "proposal_checks_used": 0, "check_problems": []}

    # 7 ------------------------------------------------------------------------------------------

    async def request_approval(self, state: AgentState) -> dict:
        if await self.s.progress.request_approval(state["run_id"], state["current_attempt_id"]) is None:
            return {"outcome": "end"}
        return {"outcome": "wait"}

    async def wait_for_approval(self, state: AgentState) -> dict:
        # Only pauses. After the resume the decision is read FROM THE DATABASE (never from the resume value).
        interrupt({"waiting_for": "approval", "attempt_id": state["current_attempt_id"]})
        run = await self.s.progress.load_run(state["run_id"])
        if run is None or run["status"] in FINAL:
            return {"outcome": "end"}
        attempt = await self.s.progress.attempt_with_edits(state["run_id"], state["current_attempt_id"])
        decision = attempt["decision"] if attempt else None
        if decision == "accepted" and run["status"] == "applying":
            return {"outcome": "accepted"}
        if decision == "rejected" and run["status"] == "searching":
            if run["reject_count"] >= self.s.settings.max_rejects:
                return _finish("stopped", "reject_limit_reached")
            return {"outcome": "rejected", "reject_feedback": attempt["decision_feedback"] or "",
                    "next_reason": "after_reject"}
        return {"outcome": "wait"}  # no decision saved yet: keep waiting

    # 8 ------------------------------------------------------------------------------------------

    async def apply_edits(self, state: AgentState) -> dict:
        run_id, attempt_id = state["run_id"], state["current_attempt_id"]
        attempt = await self.s.progress.attempt_with_edits(run_id, attempt_id)
        edits = [Edit.from_dict(e) for e in attempt["edits"]]
        settings = self.s.settings
        code_dir = self._code_dir(state)

        def plan_and_write() -> int:
            planned = plan_edits(code_dir, edits, settings.max_edits_per_attempt, settings.max_changed_lines)
            write_planned(planned)
            return len(planned)

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, plan_and_write)
        try:
            files = await asyncio.shield(future)
        except asyncio.CancelledError:
            # A cancel never leaves the files half written: let the writing finish, then stop.
            await asyncio.wait({future}, timeout=60)
            raise
        except EditError as exc:
            await self._error_event(run_id, "The approved changes could not be applied: " + "; ".join(exc.problems))
            return _finish("failed", "apply_failed")
        await self.s.progress.mark_attempt(attempt_id, "applied")
        await self.s.progress.emit(run_id, "tool_result", f"Applied {len(edits)} edits in {files} files.",
                                   {"edits": len(edits), "files": files}, attempt_id)
        return {"outcome": "ok"}

    # 9 ------------------------------------------------------------------------------------------

    async def run_tests_after_fix(self, state: AgentState) -> dict:
        run_id, attempt_id = state["run_id"], state["current_attempt_id"]
        if await self.s.progress.change_status(run_id, ("applying",), "testing") is None:
            return {"outcome": "end"}
        attempt = await self.s.progress.attempt_with_edits(run_id, attempt_id)
        outcome = await self._run_tests(state, attempt_id, "after the fix",
                                        f"attempt_{attempt['attempt_number']}.log")
        if outcome.status == "error":
            # Docker could not run the tests: a technical problem, not a failed attempt (it stays "applied").
            await self._error_event(run_id, f"The tests could not be started ({outcome.note}).")
            return _finish("failed", "test_runner_error")
        passed = outcome.status == "passed"
        return {"outcome": "passed" if passed else "failed",
                "last_test_failure": "" if passed else f"{outcome.status} (exit code {outcome.exit_code}). End of the "
                                                       f"log:\n{outcome.log_excerpt[-FAILURE_EXCERPT_CHARS:]}"}

    # 10 -----------------------------------------------------------------------------------------

    async def decide_next(self, state: AgentState) -> dict:
        run_id, attempt_id = state["run_id"], state["current_attempt_id"]
        progress = self.s.progress
        if state.get("outcome") == "passed":
            await progress.mark_attempt(attempt_id, "tests_passed")
            return _finish("succeeded", None)
        await progress.mark_attempt(attempt_id, "tests_failed")
        try:
            await asyncio.to_thread(self.s.workspaces.undo, self._code_dir(state))
        except WorkspaceError as exc:
            await self._error_event(run_id, exc.message)
            return _finish("failed", "undo_failed")
        await progress.set_reverted(attempt_id)
        failed = await progress.increment(run_id, "failed_test_count")
        if failed is not None and failed >= self.s.settings.max_failed_test_tries:
            return _finish("failed", "tests_failed_3_times")
        if await progress.change_status(run_id, ("testing",), "searching") is None:
            return {"outcome": "end"}
        return {"outcome": "retry", "next_reason": "after_test_failure"}

    # 11 -----------------------------------------------------------------------------------------

    async def finish(self, state: AgentState) -> dict:
        """The shared finish function (package, final status, copy deleted: the finish hooks). The LangGraph
        snapshot is deleted by the AgentRunner after the graph has ended."""
        await self.s.progress.finish(state["run_id"], state["final_status"], state.get("stop_reason"))
        return {}


def _attempt_line(attempt: dict) -> str:
    places = ", ".join(f"{e['file_path']} lines {e['start_line']}-{e['end_line']}" for e in attempt["edits"])
    result = attempt["status"]
    if attempt["decision"] == "rejected":
        result = f"rejected by the user: {attempt['decision_feedback'] or ''}"
    return f"Attempt {attempt['attempt_number']} ({places}): {attempt['explanation'] or ''} -> {result}"[:600]
