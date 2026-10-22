"""Phase B5 (part 2): the live stream (SSE), the timeout job, startup recovery, and load tests."""

import asyncio
import os
import signal
import statistics
import subprocess
import sys
import time
import tracemalloc

import httpx
import pytest

from app.repositories import event_repository
from app.services.jobs import recover_interrupted_runs, sweep_timeouts
from tests.fake_agent import FakeWorkflow
from tests.factories import insert_attempt, insert_project, insert_run, user_with_token
from tests.load import BACKEND_DIR, free_port, p95
from tests.sse import AsgiStream, parse_sse
from tests.test_b5_runs import BUG, FINAL, events_of, install, run_row, start, wait_for_status, waiting_run


@pytest.fixture
async def alice(db):
    return await user_with_token(db, name="Alice", is_email_verified=True)


@pytest.fixture
async def project(db, alice):
    return await insert_project(db, alice["id"])


def stream(app, user, run_id, last_event_id: str | None = None) -> AsgiStream:
    headers = dict(user["headers"])
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    return AsgiStream(app, f"/api/v1/runs/{run_id}/events", headers)


async def finished_run_with_events(app, db, user, project, count: int) -> dict:
    run = await insert_run(db, user["id"], project["id"], status="searching")
    for n in range(count - 1):
        await app.state.run_progress.emit(run["id"], "tool_called", f"step {n + 1}")
    await app.state.run_progress.finish(run["id"], "failed", "llm_error")
    return run


# --- The live stream ------------------------------------------------------------------------------

async def test_B5_F11_F13_stream_from_the_start_until_the_final_event(app, db, alice, project):
    install(app, delay=0.02, test_seconds=0.05)
    from tests.conftest import make_client

    async with make_client(app) as client:
        run = (await start(client, alice, project["id"])).json()["data"]
        async with stream(app, alice, run["id"]) as live:
            assert live.status == 200 and live.response_headers["content-type"].startswith("text/event-stream")
            await wait_for_status(db, run["id"], "waiting_for_approval")
            detail = (await client.get(f"/api/v1/runs/{run['id']}", headers=alice["headers"])).json()["data"]
            await client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                              json={"attempt_id": detail["attempts"][0]["id"], "decision": "accepted"})
            text = await live.read_until_closed(timeout=10)
            assert live.closed_by_server  # B5-F13: the server closes after the final event
    events, _ = parse_sse(text)
    stored = await events_of(db, run["id"])
    assert [e["id"] for e in events] == [e["sequence_number"] for e in stored]
    for sent, saved in zip(events, stored):
        assert sent["event"] == saved["event_type"]
        assert set(sent["data"]) == {"sequence_number", "event_type", "message", "details", "attempt_id", "created_at"}
        assert sent["data"]["sequence_number"] == sent["id"] and sent["data"]["message"] == saved["message"]
    assert events[-1]["event"] == "status_changed" and events[-1]["data"]["details"]["status"] == "succeeded"
    approval = [e for e in events if e["event"] == "approval_requested"][0]
    assert approval["data"]["attempt_id"] == detail["attempts"][0]["id"]


async def test_B5_F12_reconnect_with_last_event_id(app, db, alice, project):
    run = await finished_run_with_events(app, db, alice, project, 30)
    async with stream(app, alice, run["id"], "17") as live:
        events, _ = parse_sse(await live.read_until_closed())
    assert [e["id"] for e in events] == list(range(18, 31))


async def test_B5_F14_connect_to_a_finished_run(app, db, alice, project):
    run = await finished_run_with_events(app, db, alice, project, 12)
    async with stream(app, alice, run["id"]) as live:
        events, _ = parse_sse(await live.read_until_closed(timeout=5))
        assert live.closed_by_server
    assert [e["id"] for e in events] == list(range(1, 13))
    # Already has everything: the stream closes at once with nothing.
    async with stream(app, alice, run["id"], "12") as live:
        assert await live.read_until_closed(timeout=5) == ""


@pytest.mark.settings(STREAM_PING_SECONDS="0.2")
async def test_B5_F15_ping_lines(app, db, alice, project):
    run, _ = await waiting_run(db, alice, project)
    async with stream(app, alice, run["id"]) as live:
        text = await live.read_for(1.1)
    _, pings = parse_sse(text)
    assert 4 <= pings <= 6  # about one every 0.2 s (shortened by the setting)
    assert ": ping\n\n" in text


@pytest.mark.parametrize("header", ["abc", "-5", "99999999999", "", "1.5"])
async def test_B5_X13_odd_last_event_ids(app, db, alice, project, header):
    run = await finished_run_with_events(app, db, alice, project, 5)
    async with stream(app, alice, run["id"], header) as live:
        assert live.status == 200
        events, _ = parse_sse(await live.read_until_closed())
    assert [e["id"] for e in events] == [1, 2, 3, 4, 5]  # treated as 0: everything is sent


async def test_B5_R01_the_browser_disconnects(app, db, alice, project):
    run, _ = await waiting_run(db, alice, project)
    live = stream(app, alice, run["id"])
    await live.__aenter__()
    await live.read_for(0.2)
    assert not live.task.done()
    await live.disconnect()
    started = time.monotonic()
    await asyncio.wait_for(live.task, timeout=2)
    assert time.monotonic() - started < 2
    await asyncio.sleep(0.1)
    leftover = [t for t in asyncio.all_tasks() if "stream" in (t.get_coro().__qualname__ or "").lower()]
    assert leftover == []


# --- Timeout job and startup recovery -------------------------------------------------------------

@pytest.mark.settings(WAITING_TIMEOUT_SECONDS="60")
async def test_B5_R02_R03_timeout_job(app, db, project, alice):
    finished = []

    async def hook(run):
        finished.append(run["id"])

    app.state.run_progress.finish_hooks.append(hook)
    bob = await user_with_token(db, is_email_verified=True)
    bob_project = await insert_project(db, bob["id"])
    old, _ = await waiting_run(db, alice, project)
    fresh = await insert_run(db, bob["id"], bob_project["id"], status="waiting_for_hint")
    await db.execute("UPDATE runs SET updated_at = now() - interval '61 seconds' WHERE id = %s", (old["id"],))
    await db.execute("UPDATE runs SET updated_at = now() - interval '59 seconds' WHERE id = %s", (fresh["id"],))

    assert await sweep_timeouts(app.state.run_progress, app.state.agent_runner) == 1
    row = await run_row(db, old["id"])
    assert (row["status"], row["stop_reason"]) == ("stopped", "timed_out")
    last = (await events_of(db, old["id"]))[-1]
    assert last["details"] == {"status": "stopped", "stop_reason": "timed_out"}
    assert finished == [old["id"]]
    # B5-R03: the newer run is untouched, and a second sweep changes nothing.
    assert (await run_row(db, fresh["id"]))["status"] == "waiting_for_hint"
    assert await sweep_timeouts(app.state.run_progress, app.state.agent_runner) == 0
    assert finished == [old["id"]] and len(await events_of(db, old["id"])) == 1


@pytest.mark.settings(WAITING_TIMEOUT_SECONDS="1", TIMEOUT_SWEEP_INTERVAL_SECONDS="0.2")
async def test_B5_R02_the_sweeper_runs_by_itself(app, db, alice, project):
    run, _ = await waiting_run(db, alice, project)
    await db.execute("UPDATE runs SET updated_at = now() - interval '5 seconds' WHERE id = %s", (run["id"],))
    row = await wait_for_status(db, run["id"], "stopped", timeout=3)
    assert row["stop_reason"] == "timed_out"


async def test_B5_R04_R05_startup_recovery(app, client, db):
    finished = []

    async def hook(run):
        finished.append(run["id"])

    app.state.run_progress.finish_hooks.append(hook)
    runs = {}
    for status in ["started", "searching", "applying", "testing", "waiting_for_approval", "waiting_for_hint"]:
        user = await user_with_token(db, is_email_verified=True)
        project = await insert_project(db, user["id"])
        run = await insert_run(db, user["id"], project["id"], status=status)
        runs[status] = (user, project, run)
    from app.core import clock

    count = await recover_interrupted_runs(app.state.run_progress, app.state.agent_runner, clock.now())
    assert count == 4
    for status in ["started", "searching", "applying", "testing"]:
        row = await run_row(db, runs[status][2]["id"])
        assert (row["status"], row["stop_reason"]) == ("failed", "server_restarted")
    for status in ["waiting_for_approval", "waiting_for_hint"]:  # B5-R05: left alone
        assert (await run_row(db, runs[status][2]["id"]))["status"] == status
    assert sorted(finished) == sorted(runs[s][2]["id"] for s in ["started", "searching", "applying", "testing"])
    install(app, passive=True)
    user, project, _ = runs["searching"]
    assert (await start(client, user, project["id"])).status_code == 201  # a new run is possible


def _server(env: dict, module: str, cwd) -> tuple[subprocess.Popen, int]:
    port = free_port()
    full = {k: v for k, v in os.environ.items() if k.upper() not in env}
    full.update(env)
    full.update({"PORT": str(port), "LOG_LEVEL": "INFO", "PYTHONPATH": str(BACKEND_DIR)})
    proc = subprocess.Popen([sys.executable, "-m", module], cwd=cwd, env=full,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=2).status_code == 200:
                return proc, port
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    proc.kill()
    raise AssertionError("server did not start: " + proc.communicate()[1][-2000:])


@pytest.mark.slow
async def test_B5_R06_kill_the_real_server_during_a_run(db, base_env, tmp_path):
    alice = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, alice["id"])
    bob = await user_with_token(db, is_email_verified=True)
    bob_run, _ = await waiting_run(db, bob, await insert_project(db, bob["id"]))

    proc, port = _server(base_env, "tests.fake_server", tmp_path)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1") as client:
            response = await client.post(f"/projects/{project['id']}/runs", json={"bug_description": BUG},
                                         headers=alice["headers"])
            assert response.status_code == 201
            run_id = response.json()["data"]["id"]
            await wait_for_status(db, run_id, "searching")
    finally:
        proc.send_signal(signal.SIGKILL)  # a hard kill: no shutdown code runs
        proc.wait(timeout=10)
    assert (await run_row(db, run_id))["status"] == "searching"

    proc, port = _server(base_env, "app", tmp_path)
    try:
        row = await wait_for_status(db, run_id, "failed", timeout=5)
        assert row["stop_reason"] == "server_restarted"
        assert (await run_row(db, bob_run["id"]))["status"] == "waiting_for_approval"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1") as client:
            again = await client.post(f"/projects/{project['id']}/runs", json={"bug_description": BUG},
                                      headers=alice["headers"])
            assert again.status_code == 201
    finally:
        proc.terminate()
        proc.communicate(timeout=20)


# --- Load -----------------------------------------------------------------------------------------

async def _users_with_projects(db, count: int) -> list[tuple[dict, dict]]:
    pairs = []
    for _ in range(count):
        user = await user_with_token(db, is_email_verified=True)
        pairs.append((user, await insert_project(db, user["id"])))
    return pairs


@pytest.mark.slow
async def test_B5_L01_100_streams_on_100_runs(app, db):
    pairs = await _users_with_projects(db, 100)
    runs = [await insert_run(db, u["id"], p["id"], status="searching") for u, p in pairs]
    progress = app.state.run_progress
    streams = [stream(app, u, r["id"]) for (u, _), r in zip(pairs, runs)]
    for live in streams:
        await live.__aenter__()

    async def emit_all(run_id):
        for n in range(20):
            await progress.emit(run_id, "tool_called", f"step {n}")
        await progress.finish(run_id, "failed", "llm_error")

    started = time.perf_counter()
    await asyncio.gather(*(emit_all(r["id"]) for r in runs))
    texts = await asyncio.gather(*(live.read_until_closed(timeout=30) for live in streams))
    for live in streams:
        await live.__aexit__(None, None, None)
    print(f"\nB5-L01: 100 streams x 21 events delivered in {time.perf_counter() - started:.2f} s")
    for text, run in zip(texts, runs):
        events, _ = parse_sse(text)
        assert [e["id"] for e in events] == list(range(1, 22)), run["id"]  # 20 steps + the final event


@pytest.mark.slow
@pytest.mark.settings(STREAM_POLL_SECONDS="1.0", DB_POOL_MAX_SIZE="10")
async def test_B5_L02_L03_other_routes_while_100_streams_wait(app, client, db, monkeypatch):
    pairs = await _users_with_projects(db, 100)
    runs = [await insert_run(db, u["id"], p["id"], status="waiting_for_approval") for u, p in pairs]
    polls = 0
    real_poll = event_repository.poll_for_stream

    async def counting_poll(*args, **kwargs):
        nonlocal polls
        polls += 1
        return await real_poll(*args, **kwargs)

    monkeypatch.setattr(event_repository, "poll_for_stream", counting_poll)
    streams = [stream(app, u, r["id"]) for (u, _), r in zip(pairs, runs)]
    for live in streams:
        await live.__aenter__()
    await asyncio.sleep(1.5)
    polls = 0
    durations = []
    window_start = time.perf_counter()
    while time.perf_counter() - window_start < 5:
        user = pairs[len(durations) % 100][0]
        t = time.perf_counter()
        assert (await client.get("/api/v1/projects", headers=user["headers"])).status_code == 200
        durations.append((time.perf_counter() - t) * 1000)
        await asyncio.sleep(0.05)
    seconds = time.perf_counter() - window_start
    stats = app.state.db_pool.get_stats()
    for live in streams:
        await live.__aexit__(None, None, None)
    durations.sort()
    print(f"\nB5-L02: list projects while 100 streams wait: p95 {p95(durations):.1f} ms, max {durations[-1]:.1f} ms; "
          f"pool size {stats.get('pool_size')}, waiting requests {stats.get('requests_waiting', 0)}")
    print(f"B5-L03: 100 waiting streams caused {polls / seconds:.0f} database queries per second "
          f"(one query per stream per poll)")
    assert durations[-1] < 300
    assert stats.get("pool_size", 0) <= 10
    assert 60 <= polls / seconds <= 140


@pytest.mark.slow
async def test_B5_L04_replay_of_5000_events(app, db, alice, project):
    run = await insert_run(db, alice["id"], project["id"], status="searching")
    async with db.cursor() as cur:
        await cur.execute(
            "INSERT INTO run_events (run_id, sequence_number, event_type, message, details) "
            "SELECT %s, g, 'tool_called', 'search_files step ' || g, '{\"files\": 3}'::jsonb "
            "FROM generate_series(1, 4999) g", (run["id"],))
    await app.state.run_progress.finish(run["id"], "failed", "llm_error")
    started = time.perf_counter()
    async with stream(app, alice, run["id"]) as live:
        events, _ = parse_sse(await live.read_until_closed(timeout=10))
    seconds = time.perf_counter() - started
    print(f"\nB5-L04: replay of 5,000 events took {seconds:.2f} s")
    assert [e["id"] for e in events] == list(range(1, 5001))
    assert seconds < 3


@pytest.mark.slow
async def test_B5_L05_20_users_start_at_the_same_time(app, client, db):
    fake = install(app, delay=0.2, tool_events=3)
    pairs = await _users_with_projects(db, 20)
    responses = await asyncio.gather(*(start(client, u, p["id"]) for u, p in pairs))
    assert [r.status_code for r in responses] == [201] * 20
    for r in responses:
        await wait_for_status(db, r.json()["data"]["id"], "waiting_for_approval", timeout=10)
    print(f"\nB5-L05: at most {fake.max_active} fake agents ran at the same moment")
    assert fake.max_active == 20


@pytest.mark.slow
async def test_B5_L06_100_runs_in_a_loop_leave_nothing_behind(app, client, db, alice, project):
    install(app, test_seconds=0)
    baseline_tasks = len(asyncio.all_tasks())

    async def one_run():
        run = (await start(client, alice, project["id"])).json()["data"]
        await wait_for_status(db, run["id"], "waiting_for_approval")
        detail = (await client.get(f"/api/v1/runs/{run['id']}", headers=alice["headers"])).json()["data"]
        await client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                          json={"attempt_id": detail["attempts"][0]["id"], "decision": "accepted"})
        await wait_for_status(db, run["id"], FINAL)

    for _ in range(10):  # warm up
        await one_run()
    await asyncio.sleep(0.1)
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(100):
        await one_run()
    await asyncio.sleep(0.1)
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    tasks = len(asyncio.all_tasks())
    print(f"\nB5-L06: 100 runs: memory {(after - before) / 1024:.0f} KiB more, tasks {baseline_tasks} -> {tasks}, "
          f"agent tasks left {app.state.agent_runner.running_count()}")
    assert app.state.agent_runner.running_count() == 0
    assert tasks <= baseline_tasks
    assert after - before < 2 * 1024 * 1024


@pytest.mark.slow
async def test_B5_L07_event_insert_speed(app, db):
    pairs = await _users_with_projects(db, 10)
    runs = [await insert_run(db, u["id"], p["id"], status="searching") for u, p in pairs]
    progress = app.state.run_progress

    async def writer(run_id):
        for n in range(100):
            await progress.emit(run_id, "tool_called", f"step {n}")

    started = time.perf_counter()
    await asyncio.gather(*(writer(r["id"]) for r in runs))
    rate = 1000 / (time.perf_counter() - started)
    print(f"\nB5-L07: {rate:.0f} events per second (10 runs x 100 events at the same time, with the row lock)")
    assert rate >= 200


@pytest.mark.slow
async def test_B5_L08_history_of_a_user_with_5000_runs(client, db, alice, project):
    async with db.cursor() as cur:
        await cur.execute(
            "INSERT INTO runs (user_id, project_id, bug_description, status, language, test_command, source_type, "
            "source_reference, llm_model, created_at) "
            "SELECT %s, %s, 'bug ' || g, 'succeeded', 'java', 't', 'github', 'https://github.com/o/r', 'm', "
            "now() - (g || ' minutes')::interval FROM generate_series(1, 5000) g", (alice["id"], project["id"]))
        await cur.execute("ANALYZE runs")
    durations = []
    for page in [1, 2, 50, 125, 250] * 20:
        t = time.perf_counter()
        response = await client.get(f"/api/v1/runs?page={page}", headers=alice["headers"])
        durations.append((time.perf_counter() - t) * 1000)
        assert len(response.json()["data"]["items"]) == 20
    durations.sort()
    print(f"\nB5-L08: history pages of a user with 5,000 runs: p50 {statistics.median(durations):.1f} ms, "
          f"p95 {p95(durations):.1f} ms")
    assert p95(durations) < 100


@pytest.mark.slow
@pytest.mark.settings(STREAM_PING_SECONDS="0.05")
async def test_B5_L09_a_long_stream_with_only_pings(app, db, alice, project):
    seconds = float(os.environ.get("SANGRA_SOAK_SECONDS", "20"))
    run, _ = await waiting_run(db, alice, project)
    async with stream(app, alice, run["id"]) as live:
        await live.read_for(2)
        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        pings_before = live.text.count(": ping")
        await live.read_for(seconds)
        live.text = live.text[-1000:]  # the test client keeps text; the server keeps nothing
        after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        alive = not live.task.done()
    pings = live.text.count(": ping") + 0
    print(f"\nB5-L09: stream open {seconds:.0f} s with only pings: memory change {(after - before) / 1024:.0f} KiB, "
          f"still open: {alive}")
    assert alive
    assert after - before < 1024 * 1024
    assert pings_before > 0 and pings > 0
