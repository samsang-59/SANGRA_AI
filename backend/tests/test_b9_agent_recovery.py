"""Phase B9 (part 2): pause and resume across restarts, security, concurrency, load, and one run with the real
workspace and the real Docker test runner."""

import asyncio
import time
import tracemalloc

import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app
from app.repositories import attempt_repository
from tests.agent_helpers import (
    FILE,
    attempts,
    decide,
    events,
    fix,
    install_agent,
    install_responder,
    investigation,
    run_row,
    snapshot_rows,
    start_run,
    statuses,
    user_and_project,
    wait_for,
    wait_idle,
)
from tests.conftest import KNOWN_SECRETS, make_client
from tests.fake_llm import answer

FINAL = ("succeeded", "failed", "stopped")
pytestmark = pytest.mark.settings(LLM_RETRY_BASE_SECONDS="0")


async def paused_at_approval(app, client, db, tmp_path, script=None, tests=None):
    llm, runner = install_agent(app, script or [*investigation(), answer(fix()), answer(fix("0.10"))],
                                tests or ["failed", "passed"])
    user, project, folder = await user_and_project(db, tmp_path)
    run_id = await start_run(client, user, project)
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    return llm, runner, user, project, folder, run_id


# --- Recovery -------------------------------------------------------------------------------------

async def test_B9_R01_a_paused_step_does_its_work_only_once(app, client, db, tmp_path):
    llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path)
    await decide(client, user, run_id, "accepted")
    await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    kinds = [e["event_type"] for e in await events(db, run_id)]
    assert kinds.count("approval_requested") == 1
    assert (await statuses(db, run_id)).count("waiting_for_approval") == 1


async def test_B9_R02_restart_while_paused_then_approve(settings, db, tmp_path):
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path)
    assert (await run_row(db, run_id))["status"] == "waiting_for_approval"  # left alone by the shutdown
    assert await snapshot_rows(db, run_id) > 0

    app = create_app(settings)  # "the server starts again"
    async with LifespanManager(app), make_client(app) as client:
        assert (await run_row(db, run_id))["status"] == "waiting_for_approval"  # recovery leaves it alone
        install_agent(app, [], ["passed"])
        await decide(client, user, run_id, "accepted")
        row = await wait_for(db, run_id, FINAL)
        await wait_idle(app, run_id)
    assert row["status"] == "succeeded" and await snapshot_rows(db, run_id) == 0


async def test_B9_R03_restart_while_the_graph_runs(settings, db, tmp_path):
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        install_agent(app, [*investigation(), answer(fix())], ["failed"], latency=30)
        user, project, folder = await user_and_project(db, tmp_path)
        run_id = await start_run(client, user, project)
        await wait_for(db, run_id, "searching")
        await asyncio.sleep(0.3)
    assert (await run_row(db, run_id))["status"] == "searching"

    app = create_app(settings)
    async with LifespanManager(app):
        row = await run_row(db, run_id)
        assert (row["status"], row["stop_reason"]) == ("failed", "server_restarted")
        assert await snapshot_rows(db, run_id) == 0
        assert row["workspace_deleted_at"] is not None
        assert not (settings.workspaces_dir / str(user["id"]) / str(run_id)).exists()


@pytest.mark.parametrize("ending", ["succeeded", "failed", "stopped"])
async def test_B9_R04_nothing_left_after_any_ending(app, client, db, tmp_path, ending):
    tests = ["failed", "passed" if ending == "succeeded" else "error"]
    llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path, tests=tests)
    if ending == "stopped":
        await client.post(f"/api/v1/runs/{run_id}/cancel", headers=user["headers"])
    else:
        await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    assert row["status"] == ending
    assert await snapshot_rows(db, run_id) == 0
    assert app.state.agent_runner.running_count() == 0 and not app.state.agent_runner._tasks


async def test_B9_R05_deleting_a_run_deletes_its_langgraph_rows(app, client, db, tmp_path):
    llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path)
    assert await snapshot_rows(db, run_id) > 0
    # A finished run whose snapshot was somehow left behind (for example a crash during the finish).
    await db.execute("UPDATE runs SET status = 'failed', stop_reason = 'internal_error' WHERE id = %s", (run_id,))
    response = await client.delete(f"/api/v1/runs/{run_id}", headers=user["headers"])
    assert response.status_code == 200
    assert await snapshot_rows(db, run_id) == 0


async def test_B9_R06_the_saved_state_holds_no_files_or_secrets(app, client, db, tmp_path):
    user = None
    llm, runner = install_agent(app, [*investigation(), answer(fix())], ["failed", "passed"])
    from tests.agent_helpers import user_and_project as make

    user, project, folder = await make(db, tmp_path)
    source = folder / FILE
    source.write_text(source.read_text() + "// FULL_FILE_MARKER_5531: not part of the findings\n")
    (folder / "README.md").write_text("README_MARKER_8812 " * 50)
    run_id = await start_run(client, user, project)
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    dump = b""
    for table, columns in [("checkpoints", "checkpoint::text, metadata::text"), ("checkpoint_blobs", "blob"),
                           ("checkpoint_writes", "blob")]:
        cur = await db.execute(f"SELECT {columns} FROM {table} WHERE thread_id = %s", (str(run_id),))
        for row in await cur.fetchall():
            for value in row.values():
                dump += value if isinstance(value, bytes) else str(value).encode()
    assert dump
    for marker in [b"FULL_FILE_MARKER_5531", b"README_MARKER_8812", b"Tests run: 1"]:
        assert marker not in dump  # no whole files, no test logs
    for secret in KNOWN_SECRETS:
        assert secret.encode() not in dump
    assert b"summerRate" in dump  # only the few lines of the findings


# --- Security -------------------------------------------------------------------------------------

async def test_B9_S01_a_forged_resume_value_changes_nothing(app, client, db, tmp_path):
    llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path)
    first = (await attempts(db, run_id))[0]
    # 1. No decision in the database: a forged "accepted" resume does not apply anything.
    app.state.agent_runner.resume(run_id, {"decision": "accepted", "attempt_id": first["id"]})
    await asyncio.sleep(0.2)
    await wait_idle(app, run_id)
    assert (await run_row(db, run_id))["status"] == "waiting_for_approval" and len(runner.calls) == 1
    # 2. The database says rejected; the resume value says accepted: the graph follows the database.
    outcome = await attempt_repository.record_approval_decision(app.state.db_pool, run_id, first["id"], "rejected",
                                                                "Wrong place")
    assert outcome["outcome"] == "ok"
    app.state.agent_runner.resume(run_id, {"decision": "accepted", "attempt_id": first["id"]})
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    rows = await attempts(db, run_id)
    assert len(rows) == 2 and rows[1]["reason_started"] == "after_reject"
    assert len(runner.calls) == 1  # nothing was applied or tested


async def test_B9_S02_the_path_guard_inside_apply_edits(app, client, db, tmp_path, settings):
    llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path)
    attempt = (await attempts(db, run_id))[0]
    await db.execute("UPDATE file_edits SET file_path = '../../outside_sangra.java' WHERE attempt_id = %s",
                     (attempt["id"],))
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "apply_failed")
    assert not list(settings.workspaces_dir.rglob("outside_sangra.java"))
    assert not list(tmp_path.rglob("outside_sangra.java"))
    error = [e for e in await events(db, run_id) if e["event_type"] == "error"][0]
    assert "path refused" in error["message"]


async def test_B9_S03_nothing_is_written_before_approval(app, client, db, tmp_path, settings):
    seen = []

    def watch(messages, tools):
        code = settings.workspaces_dir / str(user["id"]) / str(run_holder[0]) / "code" / FILE
        seen.append(code.read_text() if code.exists() else None)
        return script.pop(0)

    script = [*investigation(), answer(fix())]
    run_holder = [0]
    llm, runner = install_agent(app, [watch, watch, watch], ["failed", "passed"])
    user, project, folder = await user_and_project(db, tmp_path)
    original = (folder / FILE).read_text()
    run_holder[0] = await start_run(client, user, project)
    run_id = run_holder[0]
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    code = settings.workspaces_dir / str(user["id"]) / str(run_id) / "code"
    assert seen == [original, original, original]  # at every LLM call
    assert runner.calls[0]["content"] == original  # at the baseline tests
    assert (code / FILE).read_text() == original  # while waiting for the approval
    import subprocess

    assert subprocess.run(["git", "status", "--porcelain"], cwd=code, capture_output=True, text=True).stdout == ""


# --- Concurrency ----------------------------------------------------------------------------------

async def test_B9_C01_two_accepts_at_once(app, client, db, tmp_path):
    llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path)
    first, second = await asyncio.gather(decide(client, user, run_id, "accepted"), decide(client, user, run_id, "accepted"))
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    assert len(runner.calls) == 2  # the baseline and ONE run after the fix: the graph continued once


async def test_B9_C02_ten_users_at_once(app, client, db, tmp_path):
    install_responder(app, latency=0.05, test_seconds=0.05)
    pairs = [await user_and_project(db, tmp_path, f"shop{n}") for n in range(10)]
    run_ids = await asyncio.gather(*(start_run(client, u, p) for u, p, _ in pairs))
    for (user, _, _), run_id in zip(pairs, run_ids):
        await wait_for(db, run_id, "waiting_for_approval", timeout=30)
        await wait_idle(app, run_id)
    await asyncio.gather(*(decide(client, u, r, "accepted") for (u, _, _), r in zip(pairs, run_ids)))
    for run_id in run_ids:
        row = await wait_for(db, run_id, FINAL, timeout=30)
        assert row["status"] == "succeeded"
    for run_id in run_ids:
        await wait_idle(app, run_id)
        own = await attempts(db, run_id)
        assert len(own) == 1 and all(e["run_id"] == run_id for e in await events(db, run_id))
        cur = await db.execute("SELECT count(*) AS n FROM test_results WHERE run_id = %s", (run_id,))
        assert (await cur.fetchone())["n"] == 2


async def test_B9_C03_approving_one_run_does_not_touch_another(app, client, db, tmp_path):
    install_responder(app)
    (user_a, project_a, _), (user_b, project_b, _) = [await user_and_project(db, tmp_path, n) for n in ("a", "b")]
    run_a, run_b = await start_run(client, user_a, project_a), await start_run(client, user_b, project_b)
    for run_id in (run_a, run_b):
        await wait_for(db, run_id, "waiting_for_approval")
        await wait_idle(app, run_id)
    events_b = len(await events(db, run_b))
    await decide(client, user_a, run_a, "accepted")
    await wait_for(db, run_a, FINAL)
    await wait_idle(app, run_a)
    assert (await run_row(db, run_b))["status"] == "waiting_for_approval"
    assert len(await events(db, run_b)) == events_b and await snapshot_rows(db, run_b) > 0


async def test_B9_C04_resuming_a_graph_that_already_ended(app, client, db, tmp_path, log_output):
    llm, runner, user, project, folder, run_id = await paused_at_approval(app, client, db, tmp_path)
    await decide(client, user, run_id, "accepted")
    await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    before = len(await events(db, run_id))
    app.state.agent_runner.resume(run_id, {"decision": "accepted"})
    await asyncio.sleep(0.2)
    await wait_idle(app, run_id)
    assert len(await events(db, run_id)) == before and (await run_row(db, run_id))["status"] == "succeeded"
    assert "nothing to resume" in log_output.getvalue()
    assert await snapshot_rows(db, run_id) == 0


# --- Load -----------------------------------------------------------------------------------------

async def _many(db, tmp_path, count: int, prefix: str):
    return [await user_and_project(db, tmp_path, f"{prefix}{n}") for n in range(count)]


@pytest.mark.slow
async def test_B9_L01_L04_fifty_runs_paused_at_approval(app, client, db, tmp_path):
    install_responder(app)
    pairs = await _many(db, tmp_path, 50, "p")
    tracemalloc.start()
    run_ids = [await start_run(client, u, p) for u, p, _ in pairs]
    for run_id in run_ids:
        await wait_for(db, run_id, "waiting_for_approval", timeout=60)
    for run_id in run_ids:
        await wait_idle(app, run_id)
    used = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    cur = await db.execute(
        "SELECT (SELECT coalesce(sum(length(checkpoint::text) + length(metadata::text)), 0) FROM checkpoints) + "
        "(SELECT coalesce(sum(length(blob)), 0) FROM checkpoint_blobs) + "
        "(SELECT coalesce(sum(length(blob)), 0) FROM checkpoint_writes) AS bytes")
    total = (await cur.fetchone())["bytes"]
    print(f"\nB9-L01: 50 runs paused at approval: {app.state.agent_runner.running_count()} running tasks; "
          f"traced memory {used / 1024:.0f} KiB")
    print(f"B9-L04: saved LangGraph state: {total / 50 / 1024:.1f} KiB per paused run (all checkpoints of the run)")
    assert app.state.agent_runner.running_count() == 0
    assert all([await snapshot_rows(db, r) > 0 for r in run_ids])
    assert total / 50 < 256 * 1024


@pytest.mark.slow
async def test_B9_L02_twenty_runs_at_the_same_time(app, client, db, tmp_path):
    install_responder(app, latency=0.2, test_seconds=0.2)
    pairs = await _many(db, tmp_path, 20, "q")
    worst = 0.0
    stop = False

    async def lag_probe():
        nonlocal worst
        while not stop:
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - started - 0.01)

    probe = asyncio.create_task(lag_probe())
    run_ids = await asyncio.gather(*(start_run(client, u, p) for u, p, _ in pairs))
    for run_id in run_ids:
        await wait_for(db, run_id, "waiting_for_approval", timeout=60)
    for run_id in run_ids:
        await wait_idle(app, run_id)
    await asyncio.gather(*(decide(client, u, r, "accepted") for (u, _, _), r in zip(pairs, run_ids)))
    for run_id in run_ids:
        assert (await wait_for(db, run_id, FINAL, timeout=60))["status"] == "succeeded"
    stop = True
    await probe
    print(f"\nB9-L02: 20 runs at the same time: worst event loop delay {worst * 1000:.1f} ms")
    assert worst < 0.1


@pytest.mark.slow
async def test_B9_L03_a_hundred_runs_one_after_another(app, client, db, tmp_path, settings):
    install_responder(app)
    user, project, _ = await user_and_project(db, tmp_path)

    async def one():
        run_id = await start_run(client, user, project)
        await wait_for(db, run_id, "waiting_for_approval", timeout=30)
        await wait_idle(app, run_id)
        await decide(client, user, run_id, "accepted")
        await wait_for(db, run_id, FINAL, timeout=30)
        await wait_idle(app, run_id)

    for _ in range(5):
        await one()
    baseline_tasks = len(asyncio.all_tasks())
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(100):
        await one()
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    cur = await db.execute("SELECT count(*) AS n FROM checkpoints")
    leftover_snapshots = (await cur.fetchone())["n"]
    folders = list((settings.workspaces_dir / str(user["id"])).glob("*")) if (settings.workspaces_dir / str(user["id"])).exists() else []
    print(f"\nB9-L03: 100 runs: memory {(after - before) / 1024:.0f} KiB more; tasks {baseline_tasks} -> "
          f"{len(asyncio.all_tasks())}; snapshot rows {leftover_snapshots}; workspace folders {len(folders)}")
    assert len(asyncio.all_tasks()) <= baseline_tasks
    assert leftover_snapshots == 0 and folders == []
    assert after - before < 4 * 1024 * 1024


# --- The real workspace and the real Docker test runner ---------------------------------------------

@pytest.mark.slow
async def test_B9_with_the_real_workspace_and_docker(app, client, db, tmp_path, monkeypatch, settings):
    from app.agent.nodes.steps import AgentServices
    from app.agent.workflow import LangGraphWorkflow
    from tests.conftest import sandbox_network_extras
    from tests.fake_llm import FakeLLM
    from app.runners.test_runner import TestRunner

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    env, mounts = sandbox_network_extras()
    docker_runner = TestRunner(settings, extra_env=env, extra_mounts=mounts, cache_prefix="sangra-test-cache")
    llm = FakeLLM([*investigation(), answer(fix())])
    services = AgentServices(app.state.run_progress, app.state.workspace_manager, docker_runner, lambda: llm, settings)
    app.state.agent_runner.workflow = LangGraphWorkflow(services, app.state.agent_workflow.checkpointer)
    user, project, folder = await user_and_project(db, tmp_path)
    await db.execute("UPDATE projects SET test_command = 'mvn -q -B test' WHERE id = %s", (project["id"],))
    run_id = await start_run(client, user, project)
    await wait_for(db, run_id, "waiting_for_approval", timeout=180)
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "accepted")
    row = await wait_for(db, run_id, FINAL, timeout=180)
    cur = await db.execute("SELECT test_type, status, docker_image FROM test_results WHERE run_id = %s ORDER BY id", (run_id,))
    results = [(r["test_type"], r["status"], r["docker_image"]) for r in await cur.fetchall()]
    print(f"\nB9 real Docker: {results} -> {row['status']}")
    assert row["status"] == "succeeded", row["stop_reason"]
    assert results == [("baseline", "failed", "maven:3.9-eclipse-temurin-17"),
                       ("after_fix", "passed", "maven:3.9-eclipse-temurin-17")]
