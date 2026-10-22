"""Load-test helpers: a real server process and a plain-socket client.

A plain-socket client is used on purpose: an httpx client in the same machine spends more CPU than the
server and would measure itself instead of the backend.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def running_server(env: dict[str, str], cwd: Path):
    """Starts `python -m app` with these settings and waits until /health answers 200. Yields the port."""
    port = free_port()
    full_env = {k: v for k, v in os.environ.items() if k.upper() not in env}
    full_env.update(env)
    full_env.update({"PORT": str(port), "LOG_LEVEL": "WARNING", "PYTHONPATH": str(BACKEND_DIR)})
    proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=cwd, env=full_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        healthy = False
        while time.monotonic() < deadline and not healthy:
            try:
                healthy = httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=2).status_code == 200
            except httpx.TransportError:
                pass
            if not healthy:
                time.sleep(0.2)
        assert healthy, "the server did not become healthy (is the test database up?)"
        yield port
    finally:
        proc.terminate()
        proc.communicate(timeout=20)


async def raw_burst(port: int, requests: list[tuple[str, dict[str, str]]]) -> tuple[list[int], list[float]]:
    """Sends every (path, headers) GET at the same moment. Returns the statuses and the sorted times in ms."""
    go = asyncio.Event()

    async def one(path: str, headers: dict[str, str]) -> tuple[int, float]:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        head = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        request = f"GET {path} HTTP/1.1\r\nHost: test\r\n{head}Connection: close\r\n\r\n".encode()
        await go.wait()
        started = time.perf_counter()
        writer.write(request)
        await writer.drain()
        data = await reader.read()
        elapsed = (time.perf_counter() - started) * 1000
        writer.close()
        return int(data.split(b" ", 2)[1]), elapsed

    tasks = [asyncio.create_task(one(path, headers)) for path, headers in requests]
    await asyncio.sleep(0.5)  # every connection is open before the burst starts
    go.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=120)
    return [status for status, _ in results], sorted(ms for _, ms in results)


def p95(durations: list[float]) -> float:
    return durations[int(len(durations) * 0.95) - 1]


async def raw_at_concurrency(port: int, requests: list[tuple[str, dict[str, str]]], concurrency: int
                             ) -> tuple[list[int], list[float], float]:
    """Sends the requests with at most `concurrency` in flight. Returns statuses, sorted times (ms), total seconds."""
    gate = asyncio.Semaphore(concurrency)

    async def one(path: str, headers: dict[str, str]) -> tuple[int, float]:
        async with gate:
            started = time.perf_counter()
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            head = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            writer.write(f"GET {path} HTTP/1.1\r\nHost: test\r\n{head}Connection: close\r\n\r\n".encode())
            await writer.drain()
            data = await reader.read()
            writer.close()
            return int(data.split(b" ", 2)[1]), (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    results = await asyncio.wait_for(asyncio.gather(*(one(p, h) for p, h in requests)), timeout=120)
    total = time.perf_counter() - started
    return [s for s, _ in results], sorted(ms for _, ms in results), total
