"""TestRunner (04_backend_design.md 6.4 step 2 and 6.5 item 4): runs the user's test command in Docker.

One container per test run, with only the run's code/ folder mounted (plus a dependency cache volume),
labelled with the run id so a cancel can stop it. Limits: time, CPUs, memory, number of processes and output
size. Nothing from the backend's environment is passed in (no secrets). The container never runs as root and
has no Linux capabilities. The result is passed / failed / timed_out / error, with the exit code, the full log
(saved to artifacts/), a log excerpt, the duration and the image used.

How results count:
- exit code 0 -> passed; any other exit code -> failed (failing tests, compile errors ...);
- exit code 126 or 127 (the command was not found or cannot run) -> error: the tests could not run at all;
- killed for using too much memory, or for printing more than the output cap -> failed (the code under test
  did it), with a note;
- over the time limit -> timed_out;
- Docker unreachable, image missing and not pullable, container could not start -> error.
"""

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from app.core.config import Settings

logger = logging.getLogger("app.tests")

LABEL_MANAGED = "sangra.managed"
LABEL_RUN = "sangra.run_id"
WORKDIR = "/workspace"
CACHE_DIR = "/cache"
NOT_RUNNABLE_EXIT_CODES = (126, 127)


@dataclass
class TestOutcome:
    status: str                      # passed | failed | timed_out | error
    exit_code: int | None
    log: str
    log_excerpt: str
    duration_ms: int
    docker_image: str
    started_at: datetime
    finished_at: datetime
    note: str | None = None          # why: "out of memory", "output limit reached", "Docker is not running" ...
    log_path: Path | None = None


@dataclass
class _Limits:
    timeout: float
    output_max: int


@dataclass
class _Watch:
    """Shared between the waiting thread and the log thread."""
    stop: threading.Event = field(default_factory=threading.Event)
    output_exceeded: bool = False


class TestRunner:
    def __init__(self, settings: Settings, *, extra_env: dict[str, str] | None = None,
                 extra_mounts: dict[str, str] | None = None, cache_prefix: str = "sangra-cache"):
        """extra_env / extra_mounts (host path -> container path, read-only) are for special machines only
        (for example a network that inspects TLS and needs its own certificate); the app does not use them."""
        self.settings = settings
        self.extra_env = dict(extra_env or {})
        self.extra_mounts = dict(extra_mounts or {})
        self.cache_prefix = cache_prefix
        self._slots = asyncio.Semaphore(settings.max_parallel_test_containers)
        self.running = 0
        self.max_running = 0

    # --- public ----------------------------------------------------------------------------------

    def image_for(self, language: str) -> str:
        return self.settings.test_image_java if language == "java" else self.settings.test_image_python

    async def run(self, *, run_id: int, user_id: int, code_dir: Path, language: str, test_command: str,
                  log_path: Path | None = None, timeout: float | None = None) -> TestOutcome:
        """Runs the test command once. Waits for a free slot first (at most MAX_PARALLEL_TEST_CONTAINERS run
        at the same moment). If the calling task is cancelled, the container is killed and removed."""
        limits = _Limits(timeout or self.settings.test_timeout_seconds, self.settings.test_output_max_bytes)
        image = self.image_for(language)
        async with self._slots:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
            watch = _Watch()
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(None, self._run_blocking, run_id, user_id, code_dir, language,
                                          test_command, image, limits, watch)
            try:
                outcome = await asyncio.shield(future)
            except asyncio.CancelledError:
                watch.stop.set()  # the thread kills and removes the container
                try:
                    await asyncio.wait_for(asyncio.shield(future), timeout=15)
                except Exception:
                    pass
                raise
            finally:
                self.running -= 1
        if log_path is not None:
            await asyncio.to_thread(_save_log, log_path, outcome.log)
            outcome.log_path = log_path
        return outcome

    async def stop_run(self, run_id: int) -> int:
        """Kills every test container of a run (cancel). Returns how many were stopped."""
        return await asyncio.to_thread(self._remove_labelled, {LABEL_RUN: str(run_id)})

    async def remove_leftovers(self) -> int:
        """At startup (Gaps #7): removes every container of ours left over after a crash."""
        return await asyncio.to_thread(self._remove_labelled, {LABEL_MANAGED: "true"})

    # --- the blocking part (runs in a thread) ----------------------------------------------------

    def _client(self) -> docker.DockerClient:
        return docker.from_env(timeout=60)

    def _run_blocking(self, run_id: int, user_id: int, code_dir: Path, language: str, test_command: str,
                      image: str, limits: _Limits, watch: _Watch) -> TestOutcome:
        started = datetime.now(timezone.utc)
        clock = time.monotonic()

        def ended(status: str, exit_code: int | None, log: str, note: str | None) -> TestOutcome:
            excerpt = log[-self.settings.test_log_excerpt_chars:]
            if note:
                excerpt = f"{excerpt}\n[SANGRA_AI: {note}]" if excerpt else f"[SANGRA_AI: {note}]"
            return TestOutcome(status, exit_code, log, excerpt, int((time.monotonic() - clock) * 1000), image,
                               started, datetime.now(timezone.utc), note)

        try:
            client = self._client()
            client.ping()
        except DockerException:
            return ended("error", None, "", "Docker is not running or cannot be reached")
        container = None
        try:
            try:
                self._ensure_image(client, image)
            except DockerException as exc:
                return ended("error", None, "", f"the test image {image} is missing and could not be pulled "
                                                f"({_short(exc)})")
            user = self._container_user()
            cache_volume = self._ensure_cache(client, image, language, user_id, user)
            self._open_for_container(code_dir, user)
            container = client.containers.create(
                image,
                command=[test_command],
                entrypoint=["/bin/sh", "-c"],
                working_dir=WORKDIR,
                user=user,
                environment=self._environment(language),
                volumes=self._volumes(code_dir, cache_volume),
                labels={LABEL_MANAGED: "true", LABEL_RUN: str(run_id), "sangra.user_id": str(user_id)},
                mem_limit=f"{self.settings.test_memory_mb}m",
                memswap_limit=f"{self.settings.test_memory_mb}m",
                nano_cpus=int(self.settings.test_cpus * 1e9),
                pids_limit=self.settings.test_pids_limit,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                network_mode="bridge",
                stdin_open=False,
                tty=False,
            )
            container.start()
            log_parts: list[str] = []
            reader = threading.Thread(target=_read_logs, args=(container, log_parts, limits.output_max, watch),
                                      daemon=True)
            reader.start()
            deadline = time.monotonic() + limits.timeout
            timed_out = False
            while True:
                container.reload()
                if container.status in ("exited", "dead"):
                    break
                if watch.stop.is_set() or watch.output_exceeded:
                    container.kill()
                    break
                if time.monotonic() > deadline:
                    timed_out = True
                    container.kill()
                    break
                time.sleep(0.2)
            reader.join(timeout=10)
            log = "".join(log_parts)
            container.reload()
            state = container.attrs.get("State", {})
            exit_code = None if timed_out else state.get("ExitCode")
            if watch.stop.is_set():
                return ended("error", exit_code, log, "stopped (the run was cancelled)")
            if timed_out:
                return ended("timed_out", None, log, f"the tests ran longer than {limits.timeout:.0f} seconds")
            if watch.output_exceeded:
                return ended("failed", exit_code, log, f"the output passed {limits.output_max} bytes, so the "
                                                      "tests were stopped")
            if state.get("OOMKilled"):
                return ended("failed", exit_code, log, f"the tests used more than {self.settings.test_memory_mb} MB "
                                                      "of memory and were stopped")
            if exit_code == 0:
                return ended("passed", 0, log, None)
            if exit_code in NOT_RUNNABLE_EXIT_CODES:
                return ended("error", exit_code, log, "the test command was not found or cannot run, "
                                                      "check the test command")
            return ended("failed", exit_code, log, None)
        except (DockerException, OSError) as exc:
            logger.warning("Run %s: the test container failed (%s)", run_id, _short(exc))
            return ended("error", None, "", f"Docker failed while running the tests ({_short(exc)})")
        finally:
            # Docker may be gone at this point (a Docker restart): cleaning up must never raise.
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.warning("Run %s: a test container could not be removed; it is removed at the next "
                                   "startup", run_id)
            try:
                client.close()
            except Exception:
                pass

    # --- helpers ---------------------------------------------------------------------------------

    def _ensure_image(self, client: docker.DockerClient, image: str) -> None:
        try:
            client.images.get(image)
        except ImageNotFound:
            logger.info("Pulling the test image %s", image)
            client.images.pull(image)

    def _container_user(self) -> str:
        """Never root (Gaps #9). "auto": the backend's own user, so files the tests create stay the
        backend's to clean up; when the backend itself runs as root, user 1000 is used instead."""
        if self.settings.test_container_user != "auto":
            return self.settings.test_container_user
        getuid = getattr(os, "getuid", None)  # not on Windows (Docker Desktop maps file owners itself)
        if getuid is None or getuid() == 0:
            return "1000:1000"
        return f"{getuid()}:{os.getgid()}"

    def _ensure_cache(self, client: docker.DockerClient, image: str, language: str, user_id: int, user: str) -> str:
        """A volume for downloaded libraries (Maven, pip), so later runs are faster. Local mode: one per
        language. Deployed mode: one per user and language, so one user's build cannot poison another's
        (04_backend_design.md 6.7)."""
        name = f"{self.cache_prefix}-{language}"
        if self.settings.app_mode == "deployed":
            name += f"-u{int(user_id)}"
        try:
            client.volumes.get(name)
        except NotFound:
            client.volumes.create(name, labels={LABEL_MANAGED: "true"})
            # A new volume belongs to root; give it to the container user once.
            client.containers.run(image, entrypoint=["/bin/sh", "-c"], command=[f"chown {user} {CACHE_DIR}"],
                                  volumes={name: {"bind": CACHE_DIR, "mode": "rw"}}, user="0:0", remove=True,
                                  network_mode="none", labels={LABEL_MANAGED: "true"})
        return name

    def _open_for_container(self, code_dir: Path, user: str) -> None:
        """If the container runs as another user than the files' owner, it must still be able to write build
        output (target/, __pycache__) into the run's own copy."""
        if user.split(":")[0] == str(getattr(os, "getuid", lambda: -1)()):
            return
        for folder, dirs, files in os.walk(code_dir):
            os.chmod(folder, 0o777)
            for name in files:
                path = os.path.join(folder, name)
                if not os.path.islink(path):
                    os.chmod(path, os.stat(path).st_mode | 0o666)

    def _environment(self, language: str) -> dict[str, str]:
        """Only what the tools need. Nothing from the backend's own environment (no secrets)."""
        env = {"HOME": CACHE_DIR, "PATH": f"{CACHE_DIR}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "CI": "true"}
        if language == "java":
            env.update({"MAVEN_CONFIG": f"{CACHE_DIR}/.m2", "MAVEN_OPTS": f"-Duser.home={CACHE_DIR}",
                        "PATH": env["PATH"] + ":/usr/share/maven/bin:/opt/java/openjdk/bin",
                        "JAVA_HOME": "/opt/java/openjdk"})
        else:
            env.update({"PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
        for key, value in self.extra_env.items():
            env[key] = f"{env[key]} {value}" if key == "MAVEN_OPTS" and key in env else value
        return env

    def _volumes(self, code_dir: Path, cache_volume: str) -> dict:
        volumes = {str(code_dir.resolve()): {"bind": WORKDIR, "mode": "rw"},
                   cache_volume: {"bind": CACHE_DIR, "mode": "rw"}}
        for host, inside in self.extra_mounts.items():
            volumes[host] = {"bind": inside, "mode": "ro"}
        return volumes

    def _remove_labelled(self, labels: dict[str, str]) -> int:
        try:
            client = self._client()
        except DockerException:
            return 0
        removed = 0
        try:
            filters = {"label": [f"{key}={value}" for key, value in labels.items()]}
            for container in client.containers.list(all=True, filters=filters):
                try:
                    container.remove(force=True)
                    removed += 1
                except (NotFound, APIError):
                    pass
        except DockerException:
            pass
        finally:
            client.close()
        if removed:
            logger.info("Removed %s test containers (%s)", removed, labels)
        return removed


def _read_logs(container, parts: list[str], output_max: int, watch: _Watch) -> None:
    """Collects stdout and stderr while the container runs; stops (and flags) at the output cap, so a test
    that prints 1 GB never fills the backend's memory."""
    size = 0
    try:
        for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=True):
            if size + len(chunk) > output_max:
                parts.append(chunk[: max(0, output_max - size)].decode("utf-8", errors="replace"))
                watch.output_exceeded = True
                return
            size += len(chunk)
            parts.append(chunk.decode("utf-8", errors="replace"))
            if watch.stop.is_set():
                return
    except Exception:  # Docker went away while streaming: the waiting loop reports it
        return


def _save_log(path: Path, log: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(log, encoding="utf-8")


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else type(exc).__name__)[:200]
