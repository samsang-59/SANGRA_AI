"""Phase B9 (part 1): the LangGraph agent, all 11 steps, with a FakeLLM and a FakeTestRunner: functional and failure."""

import asyncio
import zipfile

import pytest

from app.agent.llm.client import TransientLLMError
from app.agent.nodes.steps import AgentSteps
from app.runners.workspace_manager import WorkspaceError, WorkspaceManager
from app.services.run_progress import NO_TEST_COVERS_LABEL
from tests.agent_helpers import (
    FILE,
    FOUND,
    NOT_FOUND,
    attempts,
    decide,
    events,
    fix,
    install_agent,
    investigation,
    prompt_text,
    run_row,
    snapshot_rows,
    start_run,
    statuses,
    user_and_project,
    wait_for,
    wait_idle,
)
from tests.factories import insert_project
from tests.fake_llm import answer, tool_call

FINAL = ("succeeded", "failed", "stopped")
pytestmark = pytest.mark.settings(LLM_RETRY_BASE_SECONDS="0")


async def happy_run(app, client, db, tmp_path, script=None, tests=None):
    llm, runner = install_agent(app, script or [*investigation(), answer(fix())], tests or ["failed", "passed"])
    user, project, folder = await user_and_project(db, tmp_path)
    run_id = await start_run(client, user, project)
    return llm, runner, user, project, folder, run_id


# --- Functional ---------------------------------------------------------------------------------------

async def test_B9_F01_happy_path(app, client, db, tmp_path, settings):
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path)
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    assert (await decide(client, user, run_id, "accepted")).status_code == 200
    await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    row = await run_row(db, run_id)  # read again: the finish steps ran after the final status
    assert (row["status"], row["stop_reason"]) == ("succeeded", None)
    assert await statuses(db, run_id) == ["started", "searching", "waiting_for_approval", "applying", "testing",
                                          "succeeded"]
    [attempt] = await attempts(db, run_id)
    assert (attempt["status"], attempt["decision"], attempt["reason_started"]) == ("tests_passed", "accepted", "first")
    assert [c["content"] for c in runner.calls] == [
        (folder / FILE).read_text(), (folder / FILE).read_text().replace("0.05", "0.10")]  # baseline, after the fix
    assert "0.05" in (folder / FILE).read_text()  # the user's original is never touched
    assert row["total_tokens"] == 300 and row["source_commit"] is None
    assert row["workspace_deleted_at"] is not None and not (settings.workspaces_dir / str(user["id"]) / str(run_id)).exists()
    assert await snapshot_rows(db, run_id) == 0
    cur = await db.execute("SELECT test_type, status, attempt_id FROM test_results WHERE run_id = %s ORDER BY id", (run_id,))
    assert [(r["test_type"], r["status"], r["attempt_id"]) for r in await cur.fetchall()] == [
        ("baseline", "failed", None), ("after_fix", "passed", attempt["id"])]


async def test_B9_F02_reject_once_then_accept(app, client, db, tmp_path):
    script = [*investigation(), answer(fix("0.15")), answer(fix("0.10"))]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script)
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "rejected", "It must be exactly 10 percent.")
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    second = (await attempts(db, run_id))[-1]
    assert second["attempt_number"] == 2 and second["reason_started"] == "after_reject"
    assert "It must be exactly 10 percent." in str(llm.requests[-1][0])  # the feedback reached the LLM
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    assert row["status"] == "succeeded" and row["reject_count"] == 1


async def test_B9_F03_tests_fail_once_then_pass(app, client, db, tmp_path):
    script = [*investigation(), answer(fix("0.15")), answer(fix("0.10"))]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script,
                                                                 ["failed", "failed", "passed"])
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "accepted")
    await wait_for(db, run_id, "waiting_for_approval", timeout=15)
    await wait_idle(app, run_id)
    first = (await attempts(db, run_id))[0]
    assert first["status"] == "tests_failed" and first["reverted_at"] is not None
    assert "Tests failed: summerSaleIsTenPercent" in str(llm.requests[-1][0])  # the test errors reached the LLM
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    assert row["status"] == "succeeded" and row["failed_test_count"] == 1
    original = (folder / FILE).read_text()
    # Before attempt 2 the copy was back to the original (the third test run saw attempt 2's change only).
    assert runner.calls[2]["content"] == original.replace("0.05", "0.10")
    assert (await attempts(db, run_id))[1]["reason_started"] == "after_test_failure"


async def test_B9_F04_nothing_found_then_a_hint(app, client, db, tmp_path):
    script = [*investigation(found=False), *investigation(found=True), answer(fix())]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script)
    await wait_for(db, run_id, "waiting_for_hint")
    await wait_idle(app, run_id)
    response = await client.post(f"/api/v1/runs/{run_id}/hint", json={"text": "Look at Discount.java"},
                                 headers=user["headers"])
    assert response.status_code == 200
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    assert "Look at Discount.java" in prompt_text(llm)
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    assert row["status"] == "succeeded" and row["hint_count"] == 1


async def test_B9_F05_baseline_already_passes(app, client, db, tmp_path):
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, tests=["passed", "passed"])
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    assert row["status"] == "succeeded" and NO_TEST_COVERS_LABEL in row["final_summary"]


async def test_B9_F06_events_of_every_step(app, client, db, tmp_path):
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path)
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "accepted")
    await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    kinds = [e["event_type"] for e in await events(db, run_id)]
    for kind in ["status_changed", "tests_started", "tests_finished", "tool_called", "tool_result",
                 "approval_requested", "approval_decided"]:
        assert kind in kinds, kind
    assert kinds.count("tests_started") == 2 and kinds[-1] == "status_changed"
    sequence = [e["sequence_number"] for e in await events(db, run_id)]
    assert sequence == list(range(1, len(sequence) + 1))
    approval = [e for e in await events(db, run_id) if e["event_type"] == "approval_requested"][0]
    assert approval["attempt_id"] == (await attempts(db, run_id))[0]["id"]
    assert "0.05" not in str(await events(db, run_id))  # events carry no code


# --- Failure: limits ------------------------------------------------------------------------------

async def test_B9_X01_three_rejects(app, client, db, tmp_path):
    script = [*investigation(), answer(fix("0.11")), answer(fix("0.12")), answer(fix("0.13"))]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script)
    for n in range(3):
        await wait_for(db, run_id, "waiting_for_approval")
        await wait_idle(app, run_id)
        await decide(client, user, run_id, "rejected", f"No, try {n} again")
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"], row["reject_count"]) == ("stopped", "reject_limit_reached", 3)


async def test_B9_X02_three_failed_test_tries(app, client, db, tmp_path):
    script = [*investigation(), answer(fix("0.11")), answer(fix("0.12")), answer(fix("0.13"))]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script,
                                                                 ["failed", "failed", "failed", "failed"])
    for _ in range(3):
        await wait_for(db, run_id, "waiting_for_approval")
        await wait_idle(app, run_id)
        await decide(client, user, run_id, "accepted")
        await wait_for(db, run_id, ("searching", "waiting_for_approval", *FINAL), timeout=15)
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"], row["failed_test_count"]) == ("failed", "tests_failed_3_times", 3)


async def test_B9_X03_a_fourth_hint_is_never_asked(app, client, db, tmp_path):
    script = investigation(found=False) * 4
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script)
    for n in range(3):
        await wait_for(db, run_id, "waiting_for_hint")
        await wait_idle(app, run_id)
        await client.post(f"/api/v1/runs/{run_id}/hint", json={"text": f"hint number {n}"}, headers=user["headers"])
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"], row["hint_count"]) == ("failed", "no_match_after_3_hints", 3)


def _too_many_edits() -> dict:
    return {"explanation": "x", "edits": [{"file_path": FILE, "start_line": n, "end_line": n, "old_code": "x",
                                           "new_code": "y"} for n in range(1, 12)]}


def _too_many_lines() -> dict:
    return {"explanation": "x", "edits": [{"file_path": FILE, "start_line": 5, "end_line": 5,
                                           "old_code": "        return 0.05;", "new_code": "\n".join(["// x"] * 400)}]}


def _overlapping() -> dict:
    return {"explanation": "x", "edits": [
        {"file_path": FILE, "start_line": 4, "end_line": 5, "old_code": "    public static double summerRate() {\n"
                                                                          "        return 0.05;", "new_code": "a"},
        {"file_path": FILE, "start_line": 5, "end_line": 6, "old_code": "        return 0.05;\n    }", "new_code": "b"}]}


@pytest.mark.parametrize("bad", [
    fix(old="        return 0.07;"),            # old_code does not match
    _too_many_edits(),                          # more than 10 edits
    _too_many_lines(),                          # more than 300 changed lines
    _overlapping(),                             # edits overlap
    fix(path="../../outside.java"),             # path outside code/
], ids=["old_code_mismatch", "too_many_edits", "too_many_lines", "overlap", "outside"])
async def test_B9_X04_three_invalid_proposals_in_a_row(app, client, db, tmp_path, bad):
    script = [*investigation(), answer(bad), answer(bad), answer(bad)]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script)
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "proposal_invalid")
    assert await attempts(db, run_id) == []  # an unchecked draft is never saved
    feedback = str(llm.requests[-1][0][-1]["content"])
    assert "Problems found in your last answer" in feedback
    if bad["edits"][0].get("old_code") == "        return 0.07;":
        assert "The real lines are:" in prompt_text(llm) and "5:         return 0.05;" in prompt_text(llm)


async def test_B9_X04_an_unreadable_proposal_also_counts(app, client, db, tmp_path):
    script = [*investigation(), *[answer("no json")] * 6]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script)
    row = await wait_for(db, run_id, FINAL)
    assert row["stop_reason"] == "proposal_invalid"


# --- Failure: technical problems ------------------------------------------------------------------

async def test_B9_X05_the_copy_cannot_be_made(app, client, db, tmp_path, settings):
    install_agent(app, [], [])
    user, project, folder = await user_and_project(db, tmp_path)
    import shutil

    shutil.rmtree(folder)  # the local folder was deleted after the project was saved
    run_id = await start_run(client, user, project)
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "clone_failed")
    error = [e for e in await events(db, run_id) if e["event_type"] == "error"][0]
    assert "does not exist" in error["message"]

    zip_project = await insert_project(db, user["id"], source_type="zip", source_reference=None)
    await db.execute("UPDATE runs SET status = 'failed' WHERE id = %s", (run_id,))
    response = await client.post(f"/api/v1/projects/{zip_project['id']}/runs", headers=user["headers"],
                                 data={"bug_description": "The summer sale discount is wrong"},
                                 files={"zip_file": ("bad.zip", b"this is not a zip", "application/zip")})
    row = await wait_for(db, response.json()["data"]["id"], FINAL)
    assert row["stop_reason"] == "clone_failed"


@pytest.mark.parametrize(("status", "reason"), [("error127", "baseline_could_not_run"), ("timed_out", "baseline_could_not_run"),
                                                ("error", "test_runner_error")])
async def test_B9_X06_baseline_cannot_run(app, client, db, tmp_path, status, reason):
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, [], [status])
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", reason)
    assert any(e["event_type"] == "error" for e in await events(db, run_id))


async def test_B9_X07_apply_fails_on_one_of_three_edits(app, client, db, tmp_path, settings):
    three = {"explanation": "x", "edits": [
        {"file_path": FILE, "start_line": 5, "end_line": 5, "old_code": "        return 0.05;", "new_code": "        return 0.10;"},
        {"file_path": "pom.xml", "start_line": 5, "end_line": 5, "old_code": "  <groupId>shop</groupId>",
         "new_code": "  <groupId>shop2</groupId>"},
        {"file_path": "src/test/java/shop/DiscountTest.java", "start_line": 1, "end_line": 1,
         "old_code": "package shop;", "new_code": "package shop; // x"}]}
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, [*investigation(), answer(three)])
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    code = settings.workspaces_dir / str(user["id"]) / str(run_id) / "code"
    test_file = code / "src/test/java/shop/DiscountTest.java"
    test_file.write_text("// changed by someone\n" + test_file.read_text())  # the third edit no longer matches
    seen = {}
    real_undo = WorkspaceManager.undo

    def watching_undo(self, code_dir):
        seen["discount"] = (code_dir / FILE).read_text()
        seen["pom"] = (code_dir / "pom.xml").read_text()
        return real_undo(self, code_dir)

    import unittest.mock as mock

    with mock.patch.object(WorkspaceManager, "undo", watching_undo):
        await decide(client, user, run_id, "accepted")
        row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "apply_failed")
    assert "0.05" in seen["discount"] and "<groupId>shop</groupId>" in seen["pom"]  # nothing was written


async def test_B9_X08_docker_cannot_start_after_the_fix(app, client, db, tmp_path):
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, tests=["failed", "error"])
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"], row["failed_test_count"]) == ("failed", "test_runner_error", 0)
    assert (await attempts(db, run_id))[0]["status"] == "applied"  # applied but never tested


async def test_B9_X09_undo_fails(app, client, db, tmp_path, monkeypatch):
    def broken_undo(self, code_dir):
        raise WorkspaceError("undo_failed", "The changes could not be fully undone (files are still changed).")

    monkeypatch.setattr(WorkspaceManager, "undo", broken_undo)
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, tests=["failed", "failed"])
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "undo_failed")


async def test_B9_X10_llm_errors_after_retries(app, client, db, tmp_path):
    errors = [TransientLLMError("RateLimitError (HTTP 429)")] * 4
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, errors)
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "llm_error")
    assert len(llm.requests) == 4


STEPS = ["prepare_workspace", "run_baseline_tests", "investigate", "request_hint", "wait_for_hint", "propose_fix",
         "check_proposal", "request_approval", "wait_for_approval", "apply_edits", "run_tests_after_fix",
         "decide_next", "finish"]


@pytest.mark.parametrize("step", STEPS)
async def test_B9_X11_an_unexpected_error_in_each_step(app, client, db, tmp_path, settings, monkeypatch, step):
    real = getattr(AgentSteps, step)

    async def broken(self, state):
        if step.startswith("wait_for"):
            await real(self, state)  # pause first; the error comes after the resume
        raise RuntimeError(f"injected into {step}")

    monkeypatch.setattr(AgentSteps, step, broken)
    found = step in ("wait_for_hint", "request_hint")
    script = [*investigation(found=not found), *investigation(), answer(fix())]
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path, script)
    row = await wait_for(db, run_id, (*FINAL, "waiting_for_hint", "waiting_for_approval"))
    await wait_idle(app, run_id)
    row = await run_row(db, run_id)
    if row["status"] == "waiting_for_hint":
        await client.post(f"/api/v1/runs/{run_id}/hint", json={"text": "Discount.java"}, headers=user["headers"])
        row = await wait_for(db, run_id, (*FINAL, "waiting_for_approval"))
        await wait_idle(app, run_id)
    if row["status"] == "waiting_for_approval":
        await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    assert (row["status"], row["stop_reason"]) == ("failed", "internal_error"), step
    assert not (settings.workspaces_dir / str(user["id"]) / str(run_id)).exists()  # the copy is deleted
    assert await snapshot_rows(db, run_id) == 0
    assert not app.state.agent_runner.is_running(run_id)
    install_agent(app, [], ["error127"])
    assert (await client.post(f"/api/v1/projects/{project['id']}/runs", json={"bug_description": "another bug here"},
                              headers=user["headers"])).status_code == 201  # a new run is possible


async def test_B9_X12_the_checkpoint_save_fails(app, client, db, tmp_path, monkeypatch):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    calls = 0
    real_put = AsyncPostgresSaver.aput

    async def failing_put(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("database error while saving the checkpoint")
        return await real_put(self, *args, **kwargs)

    monkeypatch.setattr(AsyncPostgresSaver, "aput", failing_put)
    llm, runner, user, project, folder, run_id = await happy_run(app, client, db, tmp_path)
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "internal_error")


# --- Failure: cancel ------------------------------------------------------------------------------

async def _cancel(client, user, run_id):
    response = await client.post(f"/api/v1/runs/{run_id}/cancel", headers=user["headers"])
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("phase", ["searching_llm", "baseline_tests", "tests_after_fix", "applying"])
async def test_B9_X13_cancel_in_each_active_state(app, client, db, tmp_path, settings, monkeypatch, phase):
    import time as _time

    from app.agent import edits as edits_module

    order = []
    if phase == "applying":
        real_write = edits_module.write_planned

        def slow_write(planned):
            order.append("write started")
            _time.sleep(1.0)
            real_write(planned)
            order.append("write finished")

        monkeypatch.setattr("app.agent.nodes.steps.write_planned", slow_write)
    real_undo = WorkspaceManager.undo

    def watching_undo(self, code_dir):
        order.append("undo")
        return real_undo(self, code_dir)

    monkeypatch.setattr(WorkspaceManager, "undo", watching_undo)
    latency = 30 if phase == "searching_llm" else 0
    test_seconds = 30 if phase in ("baseline_tests", "tests_after_fix") else 0
    llm, runner = install_agent(app, [*investigation(), answer(fix())], ["failed", "passed"], latency=latency,
                                test_seconds=test_seconds)
    user, project, folder = await user_and_project(db, tmp_path)
    if phase == "tests_after_fix":
        install_agent(app, [*investigation(), answer(fix())], ["failed", "passed"])
    run_id = await start_run(client, user, project)
    if phase in ("tests_after_fix", "applying"):
        await wait_for(db, run_id, "waiting_for_approval")
        await wait_idle(app, run_id)
        if phase == "tests_after_fix":
            app.state.agent_runner.workflow.services.test_runner.seconds = 30
        await decide(client, user, run_id, "accepted")
        await wait_for(db, run_id, "testing" if phase == "tests_after_fix" else "applying")
        await asyncio.sleep(0.3)
    else:
        await wait_for(db, run_id, "searching" if phase == "searching_llm" else "started")
        await asyncio.sleep(0.3)
    started = _time.monotonic()
    await _cancel(client, user, run_id)
    assert _time.monotonic() - started < 5
    row = await run_row(db, run_id)
    assert (row["status"], row["stop_reason"]) == ("stopped", "cancelled_by_user")
    assert not app.state.agent_runner.is_running(run_id)
    assert not (settings.workspaces_dir / str(user["id"]) / str(run_id)).exists()
    assert await snapshot_rows(db, run_id) == 0
    if phase in ("baseline_tests", "tests_after_fix"):
        assert app.state.agent_runner.workflow.services.test_runner.cancelled == 1  # the test run was stopped
    if phase == "applying":
        # Fully applied, then undone; never half written.
        assert order == ["write started", "write finished", "undo"]


@pytest.mark.parametrize("pause", ["waiting_for_approval", "waiting_for_hint"])
async def test_B9_X14_cancel_while_paused(app, client, db, tmp_path, settings, pause):
    found = pause == "waiting_for_approval"
    llm, runner, user, project, folder, run_id = await happy_run(
        app, client, db, tmp_path, [*investigation(found=found), answer(fix())])
    await wait_for(db, run_id, pause)
    await wait_idle(app, run_id)
    assert await snapshot_rows(db, run_id) > 0
    await _cancel(client, user, run_id)
    row = await run_row(db, run_id)
    assert (row["status"], row["stop_reason"]) == ("stopped", "cancelled_by_user")
    assert await snapshot_rows(db, run_id) == 0  # the paused snapshot is deleted
    assert not (settings.workspaces_dir / str(user["id"]) / str(run_id)).exists()
