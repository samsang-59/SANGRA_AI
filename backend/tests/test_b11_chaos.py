"""Phase B11 (part 3): chaos tests (B11-R01..R05): the database, Docker, the disk, Groq or the email service
go away in the middle of the work."""

import asyncio
import os
import subprocess
import time

import pytest
from asgi_lifespan import LifespanManager

from app.agent.llm.client import GroqChatModel
from app.agent.nodes.steps import AgentServices
from app.agent.workflow import LangGraphWorkflow
from app.main import create_app
from app.repositories.run_repository import ACTIVE_STATUSES
from tests.agent_helpers import (
    FakeTestRunner,
    decide,
    events,
    install_responder,
    run_row,
    start_run,
    user_and_project,
    wait_for,
    wait_idle,
)
from tests.conftest import make_client, sandbox_network_extras, url_through_proxy
from tests.factories import insert_project, user_with_token
from tests.load import free_port
from tests.sample_projects import PYTHON_COMMAND, java_project, python_project
from tests.test_b7_test_runner import _restart_docker

FINAL = ("succeeded", "failed", "stopped")
pytestmark = pytest.mark.settings(LLM_RETRY_BASE_SECONDS="0")
DB_OUTAGE_SECONDS = float(os.environ.get("SANGRA_CHAOS_DB_SECONDS", "60"))


def assert_error_shape(response) -> None:
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}, body
    assert "psycopg" not in response.text and "Traceback" not in response.text


# --- R01 ------------------------------------------------------------------------------------------------------

@pytest.mark.slow
async def test_B11_R01_database_stops_for_a_minute_during_five_runs(make_settings, migrated_database, db, db_proxy,
                                                                   tmp_path):
    settings = make_settings({"DATABASE_URL": url_through_proxy(migrated_database, db_proxy.port),
                              "DB_POOL_TIMEOUT_SECONDS": "2", "LLM_RETRY_BASE_SECONDS": "0"})
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        install_responder(app, latency=1.0, test_seconds=0.5)
        pairs = [await user_and_project(db, tmp_path, f"chaos{n}") for n in range(5)]
        run_ids = [await start_run(client, u, p) for u, p, _ in pairs]
        for run_id in run_ids:
            await wait_for(db, run_id, "searching")
        await asyncio.sleep(0.5)

        db_proxy.go_down()
        started = time.monotonic()
        answers = []
        while time.monotonic() - started < DB_OUTAGE_SECONDS:
            for path, headers in (("/api/v1/health", {}), ("/api/v1/runs", pairs[0][0]["headers"])):
                t0 = time.monotonic()
                response = await client.get(path, headers=headers)
                answers.append((path, response.status_code, time.monotonic() - t0))
                assert response.status_code in (500, 503), (path, response.status_code)
                assert_error_shape(response)
            await asyncio.sleep(2)
        during = {(p, s) for p, s, _ in answers}
        slowest = max(t for _, _, t in answers)
        db_proxy.come_back()

        deadline = time.monotonic() + 30
        while (await client.get("/api/v1/health")).status_code != 200:
            assert time.monotonic() < deadline, "the app did not recover after the database came back"
            await asyncio.sleep(0.5)
        recovered_after = time.monotonic() - started - DB_OUTAGE_SECONDS
        assert (await client.get("/api/v1/runs", headers=pairs[0][0]["headers"])).status_code == 200

        # Every run ends in a clear state: finished, or paused at approval with its snapshot saved (then approved).
        states = {}
        for run_id in run_ids:
            row = await wait_for(db, run_id, ("waiting_for_approval", *FINAL), timeout=60)
            await wait_idle(app, run_id, timeout=30)
            states[run_id] = (row["status"], row["stop_reason"])
        for (user, _, _), run_id in zip(pairs, run_ids):
            if states[run_id][0] == "waiting_for_approval":
                assert (await decide(client, user, run_id, "accepted")).status_code == 200
                row = await wait_for(db, run_id, FINAL, timeout=30)
                states[run_id] = (row["status"], row["stop_reason"])
        for run_id in run_ids:
            await wait_idle(app, run_id, timeout=30)
        cur = await db.execute("SELECT count(*) AS n FROM runs WHERE id = ANY(%s) AND status = ANY(%s)",
                               (run_ids, list(ACTIVE_STATUSES)))
        assert (await cur.fetchone())["n"] == 0
        # A new run works normally after the outage.
        user, project, _ = await user_and_project(db, tmp_path, "after")
        fresh = await start_run(client, user, project)
        await wait_for(db, fresh, "waiting_for_approval", timeout=30)
    print(f"\nB11-R01: database down {DB_OUTAGE_SECONDS:.0f} s during 5 runs; answers while down: {sorted(during)}, "
          f"slowest {slowest:.1f} s; healthy again {recovered_after:.1f} s after it came back; "
          f"runs ended as {sorted(states.values(), key=str)}")


# --- R02 ------------------------------------------------------------------------------------------------------

@pytest.mark.slow
async def test_B11_R02_docker_stops_in_the_middle_of_a_test_run(app, client, db, tmp_path, monkeypatch, settings):
    from app.runners.test_runner import TestRunner
    from tests.fake_llm import FakeLLM, answer, tool_call

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    env, mounts = sandbox_network_extras()
    runner = TestRunner(settings, extra_env=env, extra_mounts=mounts, cache_prefix="sangra-test-cache")
    found = {"outcome": "found", "summary": "summer_rate",
             "findings": [{"file_path": "discount.py", "start_line": 1, "end_line": 2, "note": "rate"}]}
    proposal = {"explanation": "Sleep, then fix.", "edits": [
        {"file_path": "discount.py", "start_line": 2, "end_line": 2, "old_code": "    return 0.05",
         "new_code": "    import time; time.sleep(60); return 0.10", "reason": "slow fix"}]}
    llm = FakeLLM([tool_call("search_files", query="summer"), answer(found), answer(proposal)] * 4)
    services = AgentServices(app.state.run_progress, app.state.workspace_manager, runner, lambda: llm, settings)
    app.state.agent_runner.workflow = LangGraphWorkflow(services, app.state.agent_workflow.checkpointer)
    user = await user_with_token(db, is_email_verified=True)
    folder = python_project(tmp_path / "py", rate="0.05")
    project = await insert_project(db, user["id"], language="python", source_type="local_path",
                                   source_reference=str(folder), test_command=PYTHON_COMMAND)
    run_id = await start_run(client, user, project)
    await wait_for(db, run_id, "waiting_for_approval", timeout=240)
    await wait_idle(app, run_id)
    await decide(client, user, run_id, "accepted")
    await wait_for(db, run_id, "testing", timeout=60)
    await asyncio.sleep(8)  # the test container is running the slow test now
    try:
        subprocess.run(["pkill", "-x", "dockerd"], check=False)
        stopped = time.monotonic()
        # Docker stays down: every later test try fails to start too.
        while True:
            row = await run_row(db, run_id)
            if row["status"] in FINAL:
                break
            if row["status"] == "waiting_for_approval":
                await wait_idle(app, run_id)
                await decide(client, user, run_id, "accepted")
            assert time.monotonic() - stopped < 180, f"the run hangs in {row['status']}"
            await asyncio.sleep(0.2)
        ended_after = time.monotonic() - stopped
        await wait_idle(app, run_id, timeout=60)
    finally:
        await asyncio.to_thread(_restart_docker)
    row = await run_row(db, run_id)
    print(f"\nB11-R02: Docker stopped during the test; the run ended {ended_after:.1f} s later as "
          f"{row['status']} ({row['stop_reason']})")
    assert (row["status"], row["stop_reason"]) == ("failed", "test_runner_error")
    assert any(e["event_type"] == "error" for e in await events(db, run_id))
    assert row["workspace_deleted_at"] is not None
    await runner.remove_leftovers()


# --- R03 ------------------------------------------------------------------------------------------------------

@pytest.fixture
def tiny_disk(tmp_path):
    """A 3 MB disk (tmpfs) for the workspaces; skipped where mounting is not allowed."""
    mount_point = tmp_path / "small-disk"
    mount_point.mkdir()
    if subprocess.run(["mount", "-t", "tmpfs", "-o", "size=3m", "tmpfs", str(mount_point)],
                      capture_output=True).returncode != 0:
        pytest.skip("cannot mount a small disk here (needs root)")
    try:
        yield mount_point
    finally:
        subprocess.run(["umount", "-l", str(mount_point)], check=False)


async def test_B11_R03_the_workspace_disk_is_full(make_settings, db, tmp_path, tiny_disk):
    settings = make_settings({"WORKSPACES_DIR": str(tiny_disk), "LLM_RETRY_BASE_SECONDS": "0"})
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        install_responder(app)
        user = await user_with_token(db, is_email_verified=True)
        folder = tmp_path / "big"
        folder.mkdir()
        for n in range(8):
            (folder / f"data{n}.txt").write_bytes(os.urandom(1024 * 1024))  # 8 MB does not fit on 3 MB
        project = await insert_project(db, user["id"], source_type="local_path", source_reference=str(folder))
        started = time.monotonic()
        run_id = await start_run(client, user, project)
        row = await wait_for(db, run_id, FINAL, timeout=30)
        await wait_idle(app, run_id)
        seconds = time.monotonic() - started
        errors = [e["message"] for e in await events(db, run_id) if e["event_type"] == "error"]
        assert (row["status"], row["stop_reason"]) == ("failed", "clone_failed")
        assert any("No space left on device" in m for m in errors), errors
        assert list(tiny_disk.rglob("*.txt")) == []  # the half copy was removed, the disk is free again
        # The next run on a project that fits works.
        small = await insert_project(db, user["id"], source_type="local_path",
                                     source_reference=str(java_project(tmp_path / "small", rate="0.05")))
        next_run = await start_run(client, user, small)
        assert (await wait_for(db, next_run, ("waiting_for_approval", *FINAL), timeout=30))["status"] == \
            "waiting_for_approval"
    print(f"\nB11-R03: workspace disk full: the run failed in {seconds:.1f} s with clone_failed: {errors[0]!r}")


# --- R04 ------------------------------------------------------------------------------------------------------

@pytest.mark.settings(LLM_RETRY_BASE_SECONDS="0.5", LLM_MAX_RETRIES="3")
async def test_B11_R04_groq_unreachable(app, client, db, tmp_path, settings):
    closed = free_port()  # nothing listens there: every call is refused, as when Groq cannot be reached
    llm = GroqChatModel(settings, base_url=f"http://127.0.0.1:{closed}")
    runner = FakeTestRunner(["failed"])
    services = AgentServices(app.state.run_progress, app.state.workspace_manager, runner, lambda: llm, settings)
    app.state.agent_runner.workflow = LangGraphWorkflow(services, app.state.agent_workflow.checkpointer)
    user, project, _ = await user_and_project(db, tmp_path)
    started = time.monotonic()
    run_id = await start_run(client, user, project)
    row = await wait_for(db, run_id, FINAL, timeout=60)
    seconds = time.monotonic() - started
    await wait_idle(app, run_id)
    assert (row["status"], row["stop_reason"]) == ("failed", "llm_error")
    errors = [e["message"] for e in await events(db, run_id) if e["event_type"] == "error"]
    detail = (await client.get(f"/api/v1/runs/{run_id}", headers=user["headers"])).json()["data"]
    assert errors and detail["final_summary"]
    print(f"\nB11-R04: Groq unreachable: retried, then the run failed after {seconds:.1f} s (llm_error); the user "
          f"sees: {errors[-1]!r} / {detail['final_summary']!r}")


# --- R05 ------------------------------------------------------------------------------------------------------

async def test_B11_R05_the_email_service_is_down(make_settings, db, log_output):
    closed = free_port()
    settings = make_settings({"MAILER_MODE": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(closed),
                              "SMTP_TIMEOUT_SECONDS": "2"})
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        registered = await client.post("/api/v1/auth/register", json={"name": "Mail Down", "email": "down@example.com",
                                                                      "password": "Str0ng-pass"})
        assert registered.status_code == 201 and registered.json()["data"]["token"]
        known = await client.post("/api/v1/auth/forgot-password", json={"email": "down@example.com"})
        unknown = await client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
        assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
        await app.state.background.drain()
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {registered.json()['data']['token']}"})
        assert me.status_code == 200
    logs = log_output.getvalue()
    failures = [line for line in logs.splitlines() if "email" in line.lower() and "fail" in line.lower()]
    print(f"\nB11-R05: email service down: register 201, forgot 200 (same answer for known and unknown); "
          f"logged: {failures[:2]}")
    assert len(failures) >= 2
