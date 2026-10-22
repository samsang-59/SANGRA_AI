"""Checks that the Docker engine is reachable (used at startup and by /health)."""

import asyncio
import logging

import docker

logger = logging.getLogger("app.docker")


def _ping(timeout: float) -> bool:
    client = None
    try:
        client = docker.from_env(timeout=timeout)
        return bool(client.ping())
    except Exception:  # Docker not installed, not running, or no permission: all mean "unreachable".
        return False
    finally:
        if client is not None:
            client.close()


async def docker_reachable(timeout: float) -> bool:
    """True when the Docker engine answers a ping. Runs in a thread so the event loop is never blocked."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_ping, timeout), timeout=timeout + 1)
    except TimeoutError:
        return False
