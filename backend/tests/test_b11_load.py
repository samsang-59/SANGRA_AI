"""Phase B11 (part 4): load (L01), soak (L02) and the limit of this machine (L03).

The full lengths of the plan (L01: 10 minutes, L02: 30 minutes) run when SANGRA_LONG_TESTS=1; otherwise the same
tests run for a short time (60 s and 90 s), so the normal test suite stays quick. The README records the numbers of a
full-length run.
"""

import asyncio
import gc
import json
import logging
import os
import random
import resource
import statistics
import threading
import time
from pathlib import Path

import bcrypt
import pytest

from tests.agent_helpers import decide, install_responder, start_run, user_and_project, wait_for, wait_idle
from tests.factories import insert_event, insert_project, insert_run, user_with_token
from tests.load import running_server

LONG = os.environ.get("SANGRA_LONG_TESTS") == "1"
FINAL = ("succeeded", "failed", "stopped")
pytestmark = [pytest.mark.slow, pytest.mark.settings(LLM_RETRY_BASE_SECONDS="0")]


async def http(port: int, method: str, path: str, headers: dict | None = None, body: dict | None = None,
               read_limit: float | None = None) -> tuple[int, bytes]:
    """One request on a new connection (Connection: close). read_limit: stop reading after that many seconds
    (an open live stream)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    head = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    if body is not None:
        head += f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: test\r\n{head}Connection: close\r\n\r\n".encode() + data)
    await writer.drain()
    try:
        if read_limit is None:
            raw = await reader.read()
        else:
            raw = b""
            try:
                async with asyncio.timeout(read_limit):
                    while chunk := await reader.read(65536):
                        raw += chunk
            except TimeoutError:
                pass
    finally:
        writer.close()
    return int(raw.split(b" ", 2)[1]), raw


# --- L01 ------------------------------------------------------------------------------------------------------

async def test_B11_L01_fifty_users_mixed_actions(base_env, db, tmp_path):
    seconds = 600 if LONG else 60
    password = "Str0ng-pass-word"
    real_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()  # the real cost, as in production
    users = []
    for n in range(50):
        user = await user_with_token(db, is_email_verified=True, password_hash=real_hash,
                                     email=f"load{n}@example.com")
        projects = [await insert_project(db, user["id"], name=f"Project {k}") for k in range(5)]
        runs = []
        for k in range(10):
            run = await insert_run(db, user["id"], projects[k % 5]["id"], status="succeeded")
            for seq in range(1, 21):
                await insert_event(db, run["id"], sequence_number=seq, message=f"step {seq}")
            runs.append(run)
        users.append((user, runs))
    env = {**base_env, "BCRYPT_ROUNDS": "12", "STREAM_POLL_SECONDS": "1",
           # 50 users from ONE address (this machine): the per-address login limit would refuse them; per-email
           # limits stay as designed.
           "RATE_LIMIT_LOGIN": "1000000"}
    timings: dict[str, list[float]] = {}
    errors: list[tuple[str, int]] = []

    async def user_loop(user: dict, runs: list[dict], port: int, stop_at: float) -> None:
        headers = dict(user["headers"])
        while time.monotonic() < stop_at:
            action = random.choices(["login", "projects", "runs", "run", "stream"], [1, 6, 6, 5, 2])[0]
            started = time.perf_counter()
            if action == "login":
                status, raw = await http(port, "POST", "/api/v1/auth/login", body={"email": user["email"],
                                                                                  "password": password})
                if status == 200:
                    token = json.loads(raw.split(b"\r\n\r\n", 1)[1])["data"]["token"]
                    headers = {"Authorization": f"Bearer {token}"}
            elif action == "projects":
                status, _ = await http(port, "GET", "/api/v1/projects", headers)
            elif action == "runs":
                status, _ = await http(port, "GET", f"/api/v1/runs?page={random.randint(1, 2)}&limit=5", headers)
            elif action == "run":
                status, _ = await http(port, "GET", f"/api/v1/runs/{random.choice(runs)['id']}", headers)
            else:  # the live stream of a finished run: every event, then the server closes it
                status, raw = await http(port, "GET", f"/api/v1/runs/{random.choice(runs)['id']}/events", headers,
                                         read_limit=10)
                assert raw.count(b"\nevent: ") == 20 or status != 200
            timings.setdefault(action, []).append((time.perf_counter() - started) * 1000)
            if status != 200:
                errors.append((action, status))
            await asyncio.sleep(random.uniform(0.2, 1.0))  # a person reads the page before the next click

    with running_server(env, tmp_path) as port:
        stop_at = time.monotonic() + seconds
        await asyncio.gather(*(user_loop(u, r, port, stop_at) for u, r in users))
    total = sum(len(v) for v in timings.values())
    lines = [f"{name}: {len(values)} calls, p50 {statistics.median(values):.0f} ms, "
             f"p95 {sorted(values)[int(len(values) * 0.95) - 1]:.0f} ms"
             for name, values in sorted(timings.items())]
    print(f"\nB11-L01: 50 users for {seconds} s: {total} requests ({total / seconds:.0f}/s), "
          f"{len(errors)} errors\n  " + "\n  ".join(lines))
    assert errors == []
    # The live stream is a long connection, not a "normal route": it stays open until the server sees the run is
    # finished (one check every STREAM_POLL_SECONDS), so its time is reported above but not held to 500 ms.
    for name in ("login", "projects", "runs", "run"):
        values = sorted(timings[name])
        assert values[int(len(values) * 0.95) - 1] < 500, name


# --- L02 ------------------------------------------------------------------------------------------------------

def _process_sample(app, settings) -> dict:
    status = Path("/proc/self/status").read_text()
    rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:"))
    return {
        "rss_mb": rss / 1024,
        "tasks": len(asyncio.all_tasks()),
        "threads": threading.active_count(),
        "fds": len(os.listdir("/proc/self/fd")),
        "folders": sum(1 for user_dir in settings.workspaces_dir.iterdir() for _ in user_dir.iterdir())
        if settings.workspaces_dir.exists() else 0,
        "agent_tasks": app.state.agent_runner.running_count(),
    }


async def test_B11_L02_soak_ten_users_scripted_runs(app, client, db, tmp_path, settings):
    seconds = int(os.environ.get("SANGRA_SOAK_SECONDS", 1800 if LONG else 90))
    llm, runner = install_responder(app, latency=0.05, test_seconds=0.05)
    pairs = [await user_and_project(db, tmp_path, f"soak{n}") for n in range(10)]
    finished = 0
    stuck: list[int] = []

    async def user_loop(user, project, stop_at):
        nonlocal finished
        while time.monotonic() < stop_at:
            run_id = await start_run(client, user, project)
            await wait_for(db, run_id, "waiting_for_approval", timeout=60)
            await wait_idle(app, run_id)
            assert (await decide(client, user, run_id, "accepted")).status_code == 200
            row = await wait_for(db, run_id, FINAL, timeout=60)
            await wait_idle(app, run_id)
            if row["status"] != "succeeded":
                stuck.append(run_id)
            finished += 1

    async def db_connections() -> int:
        cur = await db.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database() "
                               "AND pid <> pg_backend_pid()")
        return (await cur.fetchone())["n"]

    # The test harness keeps every log line in memory (pytest's log capture), which would show as growth here;
    # INFO lines are switched off during the soak so only the app itself is measured.
    logging.disable(logging.INFO)
    samples = []
    stop_at = time.monotonic() + seconds
    loops = asyncio.gather(*(user_loop(u, p, stop_at) for u, p, _ in pairs))
    every = max(seconds / 10, 5)
    next_sample = time.monotonic() + every
    while not loops.done():
        await asyncio.sleep(0.5)
        if time.monotonic() >= next_sample:
            llm.requests.clear()  # the fakes record every call; that is test bookkeeping, not the app
            runner.calls.clear()
            gc.collect()
            samples.append({**_process_sample(app, settings), "db": await db_connections(), "runs": finished})
            next_sample += every
    try:
        await loops
    finally:
        logging.disable(logging.NOTSET)
    await asyncio.sleep(0.5)
    gc.collect()
    end = {**_process_sample(app, settings), "db": await db_connections(), "runs": finished}
    cur = await db.execute("SELECT count(*) AS n FROM runs WHERE status NOT IN ('succeeded', 'failed', 'stopped')")
    active = (await cur.fetchone())["n"]
    print(f"\nB11-L02: soak {seconds} s, 10 users, {finished} runs finished; samples (rss MB, tasks, threads, fds, "
          f"db connections, folders):")
    for s in [*samples, end]:
        print(f"  runs {s['runs']:5d}: {s['rss_mb']:7.1f} MB, {s['tasks']:3d} tasks, {s['threads']:3d} threads, "
              f"{s['fds']:4d} fds, {s['db']:3d} db, {s['folders']:3d} folders, {s['agent_tasks']} agent tasks")
    assert stuck == [] and active == 0
    assert end["folders"] == 0 and end["agent_tasks"] == 0
    first = samples[1] if len(samples) > 1 else samples[0]  # after the warm-up
    assert end["tasks"] <= first["tasks"] and end["threads"] <= first["threads"] + 2
    assert end["fds"] <= first["fds"] + 10
    assert end["db"] <= settings.db_pool_max_size + 10 + 2  # our pool + the LangGraph pool + the test connection
    growth = end["rss_mb"] - first["rss_mb"]
    per_run = growth * 1024 / max(end["runs"] - first["runs"], 1)
    print(f"  memory after the warm-up: {growth:+.1f} MB over {end['runs'] - first['runs']} runs "
          f"({per_run:.1f} KB per run)")
    assert per_run < 20  # a leak of a whole run (its state, files or snapshots) would be far more


# --- L03 ------------------------------------------------------------------------------------------------------

async def test_B11_L03_the_limit_of_this_machine(app, client, db, tmp_path, settings):
    """Runs in parallel (fake model and fake tests, so the backend itself is measured) are doubled until
    something breaks: an error, an event loop delay over 1 s, a detail request slower than 2 s, or 120 s per step."""
    install_responder(app, latency=0.2, test_seconds=0.2)
    steps = [10, 20, 40, 80, 160, 320] if LONG else [10, 20, 40, 80]
    results = []
    created = 0
    for size in steps:
        pairs = [await user_and_project(db, tmp_path, f"lim{created + n}") for n in range(size)]
        created += size
        worst_lag = 0.0
        detail_times: list[float] = []
        stop = False

        async def lag_probe():
            nonlocal worst_lag
            while not stop:
                t0 = time.perf_counter()
                await asyncio.sleep(0.05)
                worst_lag = max(worst_lag, time.perf_counter() - t0 - 0.05)

        probe = asyncio.create_task(lag_probe())
        started = time.perf_counter()
        broke = None
        try:
            run_ids = await asyncio.gather(*(start_run(client, u, p) for u, p, _ in pairs))
            for run_id in run_ids:
                await wait_for(db, run_id, "waiting_for_approval", timeout=120)
            for run_id in run_ids:
                await wait_idle(app, run_id, timeout=60)
            answers = await asyncio.gather(*(decide(client, u, r, "accepted") for (u, _, _), r in
                                             zip(pairs, run_ids)))
            if any(a.status_code != 200 for a in answers):
                broke = f"approval answers {sorted({a.status_code for a in answers})}"
            for (user, _, _), run_id in zip(pairs, run_ids):
                t0 = time.perf_counter()
                assert (await client.get(f"/api/v1/runs/{run_id}", headers=user["headers"])).status_code == 200
                detail_times.append(time.perf_counter() - t0)
            for run_id in run_ids:
                row = await wait_for(db, run_id, FINAL, timeout=120)
                if row["status"] != "succeeded":
                    broke = broke or f"run ended {row['status']} ({row['stop_reason']})"
            for run_id in run_ids:
                await wait_idle(app, run_id, timeout=60)
        except AssertionError as exc:
            broke = f"timeout: {str(exc)[:80]}"
        stop = True
        await probe
        seconds = time.perf_counter() - started
        if worst_lag > 1.0:
            broke = broke or f"event loop delay {worst_lag:.1f} s"
        if detail_times and max(detail_times) > 2.0:
            broke = broke or f"run detail took {max(detail_times):.1f} s"
        rss = next(int(line.split()[1]) for line in Path("/proc/self/status").read_text().splitlines()
                   if line.startswith("VmRSS:")) / 1024
        results.append((size, seconds, worst_lag, max(detail_times) if detail_times else None, rss, broke))
        if broke:
            break
    print("\nB11-L03: runs in parallel (fake model 0.2 s per call, fake tests 0.2 s), one process:")
    for size, seconds, lag, detail, rss, broke in results:
        print(f"  {size:4d} runs: all finished in {seconds:5.1f} s, worst loop delay {lag * 1000:6.0f} ms, "
              f"slowest detail {detail * 1000 if detail else 0:6.0f} ms, memory {rss:6.0f} MB"
              + (f"  BROKE: {broke}" if broke else ""))
    print(f"  open file limit {resource.getrlimit(resource.RLIMIT_NOFILE)[0]}, db pool {settings.db_pool_max_size}, "
          f"parallel test containers {settings.max_parallel_test_containers}")
    assert results[0][5] is None, "even the first step broke"
