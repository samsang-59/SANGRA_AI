"""Phase B7: the Docker test runner (real Docker)."""

import asyncio
import subprocess
import time
import uuid
from pathlib import Path

import docker
import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app
from app.runners.test_runner import LABEL_MANAGED, LABEL_RUN, TestRunner
from tests.conftest import TEST_DATABASE_URL, TEST_GROQ_KEY, TEST_JWT_SECRET, sandbox_network_extras
from tests.sample_projects import JAVA_COMMAND, PYTHON_COMMAND, java_project, python_project


@pytest.fixture(autouse=True)
def real_docker(monkeypatch):
    """These tests need the real Docker engine (the other phases pretend it is unreachable)."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    client = docker.from_env()
    client.ping()
    yield client
    client.close()


def our_containers(client, run_id: int | None = None) -> list:
    label = f"{LABEL_RUN}={run_id}" if run_id is not None else f"{LABEL_MANAGED}=true"
    return client.containers.list(all=True, filters={"label": label})


def run_id() -> int:
    return int(uuid.uuid4().int % 1_000_000_000)


def plain_folder(tmp_path: Path, name: str = "plain") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "README.txt").write_text("a project")
    return folder


async def run(runner: TestRunner, folder: Path, command: str, language: str = "python", rid: int | None = None,
              **kwargs):
    return await runner.run(run_id=rid or run_id(), user_id=1, code_dir=folder, language=language,
                            test_command=command, **kwargs)


# --- Functional ---------------------------------------------------------------------------------------

@pytest.mark.slow
async def test_B7_F01_java_maven_project_passes(docker_runner, tmp_path):
    outcome = await run(docker_runner, java_project(tmp_path / "shop"), JAVA_COMMAND, language="java")
    assert outcome.status == "passed", outcome.log_excerpt
    assert outcome.exit_code == 0 and outcome.docker_image == "maven:3.9-eclipse-temurin-17"


@pytest.mark.slow
async def test_B7_F02_python_pytest_project_passes(docker_runner, tmp_path):
    outcome = await run(docker_runner, python_project(tmp_path / "shop"), PYTHON_COMMAND)
    assert outcome.status == "passed", outcome.log_excerpt
    assert "1 passed" in outcome.log and outcome.docker_image == "python:3.12-slim"


@pytest.mark.slow
async def test_B7_F03_a_failing_test(docker_runner, tmp_path):
    java = await run(docker_runner, java_project(tmp_path / "java", rate="0.05"), JAVA_COMMAND, language="java")
    assert java.status == "failed" and java.exit_code != 0
    assert "summerSaleIsTenPercent" in java.log_excerpt
    python = await run(docker_runner, python_project(tmp_path / "py", rate="0.05"), PYTHON_COMMAND)
    assert python.status == "failed" and "test_summer_sale_is_ten_percent" in python.log_excerpt


async def test_B7_F04_F05_result_details_and_no_container_left(docker_runner, real_docker, tmp_path, settings):
    rid = run_id()
    log_path = settings.workspaces_dir / "artifacts" / "baseline.log"
    outcome = await run(docker_runner, plain_folder(tmp_path), "echo hello from the tests; exit 3", rid=rid,
                        log_path=log_path)
    assert outcome.status == "failed" and outcome.exit_code == 3
    assert outcome.duration_ms > 0 and outcome.docker_image == "python:3.12-slim"
    assert outcome.started_at <= outcome.finished_at
    assert log_path.read_text() == outcome.log and "hello from the tests" in outcome.log
    assert outcome.log_path == log_path and "hello from the tests" in outcome.log_excerpt
    assert our_containers(real_docker, rid) == []  # B7-F05: the container is removed


# --- Failure --------------------------------------------------------------------------------------

async def test_B7_X01_test_command_not_found_is_an_error(docker_runner, tmp_path):
    outcome = await run(docker_runner, plain_folder(tmp_path), "mvnn test")  # a typo
    assert outcome.status == "error" and outcome.exit_code == 127
    assert "not found" in outcome.note


@pytest.mark.slow
async def test_B7_X02_compile_error_is_a_failed_test(docker_runner, tmp_path):
    outcome = await run(docker_runner, java_project(tmp_path / "shop", broken=True), JAVA_COMMAND, language="java")
    assert outcome.status == "failed"
    assert "COMPILATION ERROR" in outcome.log_excerpt or "cannot find symbol" in outcome.log_excerpt


async def test_B7_X03_infinite_loop_times_out(docker_runner, real_docker, tmp_path):
    rid = run_id()
    started = time.monotonic()
    outcome = await run(docker_runner, plain_folder(tmp_path), "python -c 'while True: pass'", rid=rid, timeout=5)
    assert outcome.status == "timed_out" and outcome.exit_code is None
    assert time.monotonic() - started < 20
    assert our_containers(real_docker, rid) == []


@pytest.mark.settings(TEST_MEMORY_MB="128")
async def test_B7_X04_a_test_that_eats_memory(docker_runner, tmp_path):
    outcome = await run(docker_runner, plain_folder(tmp_path),
                        "python -c 'x = bytearray(600 * 1024 * 1024); print(len(x))'")
    assert outcome.status == "failed" and "more than 128 MB of memory" in outcome.note


@pytest.mark.settings(TEST_PIDS_LIMIT="64")
async def test_B7_X05_a_fork_bomb(docker_runner, tmp_path):
    bomb = ("python -c \"import os, time\n"
            "for _ in range(10000):\n"
            "    try:\n"
            "        if os.fork() == 0:\n"
            "            time.sleep(30); os._exit(0)\n"
            "    except OSError as e:\n"
            "        print('fork refused:', e); raise SystemExit(1)\n\"")
    started = time.monotonic()
    outcome = await run(docker_runner, plain_folder(tmp_path), bomb, timeout=60)
    assert outcome.status in ("failed", "timed_out")
    assert "fork refused" in outcome.log
    assert time.monotonic() - started < 60


async def test_B7_X06_one_gigabyte_of_output_is_capped(docker_runner, real_docker, tmp_path):
    import tracemalloc

    rid = run_id()
    tracemalloc.start()
    outcome = await run(docker_runner, plain_folder(tmp_path), "head -c 1073741824 /dev/zero | tr '\\0' 'x'", rid=rid,
                        timeout=120)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert outcome.status == "failed" and "output passed" in outcome.note
    assert len(outcome.log.encode()) <= 1024 * 1024
    assert peak < 20 * 1024 * 1024  # the backend never held more than a few MB
    assert our_containers(real_docker, rid) == []


async def test_B7_X07_docker_is_not_running(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/docker.sock")
    outcome = await run(TestRunner(settings), plain_folder(tmp_path), "echo hi")
    assert outcome.status == "error" and "Docker is not running" in outcome.note


@pytest.mark.settings(TEST_IMAGE_PYTHON="sangra-no-such-image-xyz:1.0")
async def test_B7_X08_missing_image(docker_runner, tmp_path):
    outcome = await run(docker_runner, plain_folder(tmp_path), "echo hi")
    assert outcome.status == "error" and "could not be pulled" in outcome.note


async def test_B7_X09_empty_project_follows_the_exit_code(docker_runner, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    outcome = await run(docker_runner, empty, "python -m unittest discover -q")
    assert outcome.status == "failed" and outcome.exit_code == 5  # unittest: "NO TESTS RAN"
    ok = await run(docker_runner, empty, "true")
    assert ok.status == "passed"


# --- Security -------------------------------------------------------------------------------------

async def test_B7_S01_the_container_sees_only_its_own_folder(docker_runner, tmp_path, settings):
    marker = tmp_path / "HOST_SECRET_MARKER.txt"
    marker.write_text("host only")
    other_run = settings.workspaces_dir / "9" / "99" / "code"
    other_run.mkdir(parents=True)
    (other_run / "OTHER_RUN.txt").write_text("other user's code")
    folder = plain_folder(tmp_path)
    probe = ("ls / ; cat /etc/hostname; "
             f"test -e {marker} && echo SAW_HOST_MARKER; "
             f"test -e {other_run}/OTHER_RUN.txt && echo SAW_OTHER_RUN; "
             "find / -name OTHER_RUN.txt 2>/dev/null; find / -name HOST_SECRET_MARKER.txt 2>/dev/null; "
             "test -e /var/run/docker.sock && echo SAW_DOCKER_SOCKET; ls /workspace")
    outcome = await run(docker_runner, folder, probe)
    assert outcome.status == "passed"
    for leak in ["SAW_HOST_MARKER", "SAW_OTHER_RUN", "SAW_DOCKER_SOCKET", "OTHER_RUN.txt", "HOST_SECRET_MARKER"]:
        assert leak not in outcome.log
    assert "README.txt" in outcome.log


async def test_B7_S02_no_secrets_in_the_environment(docker_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", TEST_GROQ_KEY)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    outcome = await run(docker_runner, plain_folder(tmp_path), "env")
    for value in [TEST_GROQ_KEY, TEST_JWT_SECRET, TEST_DATABASE_URL, "GROQ", "JWT_SECRET", "DATABASE_URL"]:
        assert value not in outcome.log
    assert "HOME=/cache" in outcome.log


async def test_B7_S03_S04_hostile_commands_only_hit_the_container(docker_runner, tmp_path, settings):
    marker = tmp_path / "STILL_HERE.txt"
    marker.write_text("host file")
    sibling = settings.workspaces_dir / "1" / "77" / "code"
    sibling.mkdir(parents=True)
    (sibling / "keep.txt").write_text("another run")
    folder = plain_folder(tmp_path, "victim")
    command = ("echo start; touch /tmp/OUTSIDE_CODE.txt; echo written > /root_owned.txt; "
               "rm -rf / --no-preserve-root 2>/dev/null; $(echo echo injected); echo done")
    outcome = await run(docker_runner, folder, command, timeout=120)
    assert marker.read_text() == "host file" and (sibling / "keep.txt").read_text() == "another run"
    assert not Path("/tmp/OUTSIDE_CODE.txt").exists() and not Path("/root_owned.txt").exists()
    assert outcome.status in ("passed", "failed")


async def test_B7_S05_the_container_user_is_not_root(docker_runner, tmp_path):
    outcome = await run(docker_runner, plain_folder(tmp_path), "id -u; id -g; cat /proc/self/status | grep CapEff")
    uid = outcome.log.split()[0]
    assert uid != "0" and outcome.status == "passed"
    assert "CapEff:\t0000000000000000" in outcome.log  # no Linux capabilities at all


# --- Concurrency ----------------------------------------------------------------------------------

async def test_B7_C01_three_runs_at_the_same_moment(settings, tmp_path, real_docker):
    env, mounts = sandbox_network_extras()
    runner = TestRunner(settings, extra_env=env, extra_mounts=mounts, cache_prefix="sangra-test-cache")
    folders = [plain_folder(tmp_path, f"p{n}") for n in range(3)]
    for n, folder in enumerate(folders):
        (folder / "id.txt").write_text(f"project {n}")
    outcomes = await asyncio.gather(*(run(runner, folder, f"cat id.txt; sleep 1; exit {n}")
                                      for n, folder in enumerate(folders)))
    for n, outcome in enumerate(outcomes):
        assert f"project {n}" in outcome.log and outcome.exit_code == n
        assert all(f"project {k}" not in outcome.log for k in range(3) if k != n)


@pytest.mark.settings(MAX_PARALLEL_TEST_CONTAINERS="2")
async def test_B7_C02_a_cap_on_parallel_containers(docker_runner, tmp_path, real_docker):
    folder = plain_folder(tmp_path)
    seen = 0

    async def sample():
        nonlocal seen
        while True:
            seen = max(seen, len(real_docker.containers.list(filters={"label": f"{LABEL_MANAGED}=true"})))
            await asyncio.sleep(0.2)

    sampler = asyncio.create_task(sample())
    outcomes = await asyncio.gather(*(run(docker_runner, folder, "sleep 2") for _ in range(5)))
    sampler.cancel()
    assert all(o.status == "passed" for o in outcomes), [(o.status, o.note, o.log[-300:]) for o in outcomes]
    assert docker_runner.max_running == 2 and seen <= 2


async def test_B7_C03_cancel_stops_the_container_quickly(docker_runner, tmp_path, real_docker):
    rid = run_id()
    task = asyncio.create_task(run(docker_runner, plain_folder(tmp_path), "sleep 300", rid=rid))
    for _ in range(100):
        if our_containers(real_docker, rid):
            break
        await asyncio.sleep(0.1)
    assert our_containers(real_docker, rid)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 5
    assert our_containers(real_docker, rid) == []
    # stop_run (by label) works too, for a container started some other way.
    real_docker.containers.run("python:3.12-slim", "sleep 300", detach=True,
                               labels={LABEL_MANAGED: "true", LABEL_RUN: str(rid)})
    assert await docker_runner.stop_run(rid) == 1 and our_containers(real_docker, rid) == []


# --- Recovery -------------------------------------------------------------------------------------

async def test_B7_R01_leftover_containers_are_removed_at_startup(settings, real_docker):
    for n in range(2):
        real_docker.containers.run("python:3.12-slim", "sleep 300", detach=True,
                                   labels={LABEL_MANAGED: "true", LABEL_RUN: str(424242 + n)})
    stranger = real_docker.containers.run("python:3.12-slim", "sleep 300", detach=True, labels={"other": "app"})
    try:
        assert len(our_containers(real_docker)) >= 2  # ours, plus any left by an earlier Docker crash
        app = create_app(settings)
        async with LifespanManager(app):
            assert our_containers(real_docker) == []
        assert stranger.status in ("created", "running") and real_docker.containers.get(stranger.id)
    finally:
        stranger.remove(force=True)


def _process_finished(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except FileNotFoundError:
        return True
    return "\nState:\tZ" in status  # exited, only not cleaned up yet


def _restart_docker() -> None:
    """Stops the Docker daemon (as a crash of Docker would) and starts it again (this cloud container)."""
    pid_file = Path("/var/run/docker.pid")
    pid = int(pid_file.read_text()) if pid_file.exists() else None
    subprocess.run(["pkill", "-x", "dockerd"], check=False)
    for _ in range(300):
        if pid is None or _process_finished(pid):
            break
        time.sleep(0.1)
    if pid_file.exists() and pid is not None and _process_finished(pid):
        pid_file.unlink()
    log = open(Path(__file__).parent / ".dockerd_restart.log", "w")
    subprocess.Popen(["dockerd"], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(300):
        if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
            return
        time.sleep(0.2)
    raise AssertionError("dockerd did not come back")


@pytest.mark.slow
async def test_B7_R02_docker_restarts_in_the_middle_of_a_test(docker_runner, tmp_path, real_docker):
    rid = run_id()
    task = asyncio.create_task(run(docker_runner, plain_folder(tmp_path), "sleep 120", rid=rid, timeout=300))
    for _ in range(100):
        if our_containers(real_docker, rid):
            break
        await asyncio.sleep(0.1)
    started = time.monotonic()
    await asyncio.to_thread(_restart_docker)
    outcome = await asyncio.wait_for(task, timeout=60)
    print(f"\nB7-R02: Docker restarted in the middle; the runner answered after {time.monotonic() - started:.1f} s "
          f"with {outcome.status} ({outcome.note})")
    assert outcome.status in ("error", "failed")
    assert time.monotonic() - started < 60
    # The crash left the container behind (Docker could not remove it): removed by label afterwards
    # (at the next backend start this happens for every leftover, B7-R01).
    await docker_runner.stop_run(rid)
    assert our_containers(real_docker, rid) == []


# --- Load -----------------------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.settings(MAX_PARALLEL_TEST_CONTAINERS="3")
async def test_B7_L01_ten_runs_queued_with_a_cap_of_three(docker_runner, tmp_path):
    import os

    folders = [python_project(tmp_path / f"p{n}") for n in range(10)]
    load_before = os.getloadavg()[0]
    started = time.perf_counter()
    outcomes = await asyncio.gather(*(run(docker_runner, f, PYTHON_COMMAND) for f in folders))
    seconds = time.perf_counter() - started
    with open("/proc/meminfo") as handle:
        meminfo = dict(line.split(":", 1) for line in handle)
    print(f"\nB7-L01: 10 pytest runs with at most 3 at once took {seconds:.1f} s; load average "
          f"{load_before:.1f} -> {os.getloadavg()[0]:.1f}; free memory {meminfo['MemAvailable'].strip()}")
    assert [o.status for o in outcomes] == ["passed"] * 10
    assert docker_runner.max_running == 3


@pytest.mark.slow
async def test_B7_L02_cold_and_warm_dependency_cache(settings, tmp_path, real_docker):
    env, mounts = sandbox_network_extras()
    prefix = f"sangra-cold-{uuid.uuid4().hex[:8]}"
    runner = TestRunner(settings, extra_env=env, extra_mounts=mounts, cache_prefix=prefix)
    try:
        times = []
        for n in range(2):
            started = time.perf_counter()
            outcome = await run(runner, java_project(tmp_path / f"shop{n}"), JAVA_COMMAND, language="java")
            times.append(time.perf_counter() - started)
            assert outcome.status == "passed", outcome.log_excerpt
        print(f"\nB7-L02: Maven project, cold cache {times[0]:.1f} s, warm cache {times[1]:.1f} s")
        assert times[1] < times[0]
    finally:
        for volume in real_docker.volumes.list(filters={"name": prefix}):
            volume.remove(force=True)
