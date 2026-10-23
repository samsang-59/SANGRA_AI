"""Helpers for the agent tests (B9): the real LangGraph agent with a scripted FakeLLM and a FakeTestRunner."""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agent.nodes.steps import AgentServices
from app.agent.workflow import LangGraphWorkflow
from app.runners.test_runner import TestOutcome
from tests.factories import insert_project, user_with_token
from tests.fake_llm import FakeLLM, answer, tool_call
from tests.sample_projects import java_project

BUG = "The summer sale shows 5% discount, it should be 10%."
FILE = "src/main/java/shop/Discount.java"
FOUND = {"outcome": "found", "summary": "summerRate returns 0.05",
         "findings": [{"file_path": FILE, "start_line": 3, "end_line": 6, "note": "the summer rate"}]}
NOT_FOUND = {"outcome": "not_found", "findings": [], "summary": "nothing about the discount"}


def fix(rate: str = "0.10", old: str = "        return 0.05;", line: int = 5, path: str = FILE) -> dict:
    return {"explanation": f"The summer rate must be {rate}.",
            "edits": [{"file_path": path, "start_line": line, "end_line": line, "old_code": old,
                       "new_code": f"        return {rate};", "reason": "the rate"}]}


def investigation(found: bool = True) -> list:
    return [tool_call("search_files", query="discount"), answer(FOUND if found else NOT_FOUND)]


class FakeTestRunner:
    """Answers the test runs from a script of statuses ("passed", "failed", "error", "error127", "timed_out"),
    and records what the code looked like at each run."""

    def __init__(self, statuses: list[str], seconds: float = 0.0):
        self.statuses = list(statuses)
        self.seconds = seconds
        self.calls: list[dict] = []
        self.cancelled = 0

    async def run(self, *, run_id, user_id, code_dir, language, test_command, log_path=None, timeout=None):
        content = (Path(code_dir) / FILE).read_text() if (Path(code_dir) / FILE).exists() else None
        self.calls.append({"run_id": run_id, "content": content, "log_path": log_path})
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        status = self.statuses.pop(0) if self.statuses else "passed"
        exit_code = {"passed": 0, "failed": 1, "error": None, "error127": 127, "timed_out": None}[status]
        note = {"error": "Docker is not running or cannot be reached",
                "error127": "the test command was not found", "timed_out": "too long"}.get(status)
        status = "error" if status == "error127" else status
        log = f"Tests {status}: summerSaleIsTenPercent" if status != "passed" else "Tests run: 1, Failures: 0"
        now = datetime.now(timezone.utc)
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(log)
        return TestOutcome(status, exit_code, log, log, 5, "fake-image", now, now, note,
                           Path(log_path) if log_path else None)


def install_agent(app, script: list, tests: list[str], *, latency: float = 0.0, test_seconds: float = 0.0,
                  forever=None) -> tuple[FakeLLM, FakeTestRunner]:
    """Puts the real LangGraph agent, with a FakeLLM and a FakeTestRunner, into the running app."""
    llm = FakeLLM(script, latency=latency, forever=forever)
    runner = FakeTestRunner(tests, seconds=test_seconds)
    services = AgentServices(app.state.run_progress, app.state.workspace_manager, runner, lambda: llm,
                             app.state.settings)
    app.state.agent_runner.workflow = LangGraphWorkflow(services, app.state.agent_workflow.checkpointer)
    return llm, runner


async def user_and_project(db, tmp_path: Path, name: str = "shop") -> tuple[dict, dict, Path]:
    """A verified user and a local-folder project holding the Amazon discount example with the 5% bug."""
    user = await user_with_token(db, is_email_verified=True)
    folder = java_project(tmp_path / f"{name}-{user['id']}", rate="0.05")
    project = await insert_project(db, user["id"], source_type="local_path", source_reference=str(folder))
    return user, project, folder


async def start_run(client, user, project) -> int:
    response = await client.post(f"/api/v1/projects/{project['id']}/runs", json={"bug_description": BUG},
                                 headers=user["headers"])
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def run_row(db, run_id: int) -> dict:
    cur = await db.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
    return await cur.fetchone()


async def wait_for(db, run_id: int, statuses, timeout: float = 15.0) -> dict:
    statuses = (statuses,) if isinstance(statuses, str) else tuple(statuses)
    deadline = time.monotonic() + timeout
    while True:
        row = await run_row(db, run_id)
        if row and row["status"] in statuses:
            return row
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} is {row and row['status']} ({row and row['stop_reason']}), "
                                 f"expected {statuses}")
        await asyncio.sleep(0.02)


async def wait_idle(app, run_id: int, timeout: float = 10.0) -> None:
    """Waits until no agent task runs for the run (the graph paused or ended)."""
    deadline = time.monotonic() + timeout
    while app.state.agent_runner.is_running(run_id):
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} is still running")
        await asyncio.sleep(0.02)


async def events(db, run_id: int) -> list[dict]:
    cur = await db.execute("SELECT * FROM run_events WHERE run_id = %s ORDER BY sequence_number", (run_id,))
    return await cur.fetchall()


async def statuses(db, run_id: int) -> list[str]:
    return [e["details"]["status"] for e in await events(db, run_id) if e["event_type"] == "status_changed"]


async def attempts(db, run_id: int) -> list[dict]:
    cur = await db.execute("SELECT * FROM attempts WHERE run_id = %s ORDER BY attempt_number", (run_id,))
    return await cur.fetchall()


async def waiting_attempt_id(client, user, run_id: int) -> int:
    detail = (await client.get(f"/api/v1/runs/{run_id}", headers=user["headers"])).json()["data"]
    return detail["attempts"][-1]["id"]


async def decide(client, user, run_id: int, decision: str, feedback: str | None = None):
    attempt_id = await waiting_attempt_id(client, user, run_id)
    body = {"attempt_id": attempt_id, "decision": decision}
    if feedback:
        body["feedback"] = feedback
    return await client.post(f"/api/v1/runs/{run_id}/approval", json=body, headers=user["headers"])


async def snapshot_rows(db, run_id: int) -> int:
    total = 0
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        cur = await db.execute(f"SELECT count(*) AS n FROM {table} WHERE thread_id = %s", (str(run_id),))
        total += (await cur.fetchone())["n"]
    return total


def prompt_text(llm: FakeLLM) -> str:
    return json.dumps(llm.requests, ensure_ascii=False)


class ResponderLLM(FakeLLM):
    """A fake model that answers from the conversation itself (no shared script), so many runs can use it at the
    same time: first a search, then "found", then the fix."""

    def __init__(self, latency: float = 0.0):
        super().__init__([], latency=latency)

    async def complete(self, messages, tools):
        self.requests.append((None, None))
        if self.latency:
            await asyncio.sleep(self.latency)
        if tools is None:
            return answer(fix())
        if not any(m.get("role") == "tool" for m in messages):
            return tool_call("search_files", query="discount")
        return answer(FOUND)


def install_responder(app, latency: float = 0.0, test_seconds: float = 0.0) -> tuple[ResponderLLM, "FakeTestRunner"]:
    llm = ResponderLLM(latency)
    runner = FakeTestRunner([], seconds=test_seconds)
    runner.statuses = None  # answers "failed" for every baseline and "passed" after a fix

    async def run(*, run_id, user_id, code_dir, language, test_command, log_path=None, timeout=None):
        fixed = "0.10" in (Path(code_dir) / FILE).read_text()
        runner.statuses = ["passed" if fixed else "failed"]
        return await FakeTestRunner.run(runner, run_id=run_id, user_id=user_id, code_dir=code_dir, language=language,
                                        test_command=test_command, log_path=log_path, timeout=timeout)

    runner.run = run
    services = AgentServices(app.state.run_progress, app.state.workspace_manager, runner, lambda: llm,
                             app.state.settings)
    app.state.agent_runner.workflow = LangGraphWorkflow(services, app.state.agent_workflow.checkpointer)
    return llm, runner
