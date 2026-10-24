"""Found by the FE7 browser tests: `python -m app` must stop on SIGTERM (Ctrl+C) even while a Run page holds a live
stream open (B0-R01 "clean shutdown"). Uvicorn waits for open requests, and a live stream never ends on its own, so
the server has a shutdown time limit (app/__main__.py SHUTDOWN_GRACE_SECONDS)."""

import json
import os
import re
import secrets
import signal
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from app.__main__ import SHUTDOWN_GRACE_SECONDS
from tests.load import free_port
from tests.sample_projects import JAVA_COMMAND, java_project

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.slow
def test_FE7_the_server_stops_on_sigterm_while_a_live_stream_is_open(empty_database, tmp_path):
    python = str(BACKEND_DIR / ".venv/bin/python")
    migrated = subprocess.run([python, "-m", "app.migrate"], cwd=tmp_path, capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(BACKEND_DIR), "DATABASE_URL": empty_database})
    assert migrated.returncode == 0, migrated.stderr
    script = tmp_path / "agent.json"
    script.write_text(json.dumps({"llm": [{"answer": {"outcome": "not_found", "findings": []}}],
                                  "tests": [{"status": "failed", "exit_code": 1, "log": "1 failed"}]}))
    port = free_port()
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), "PYTHONPATH": str(BACKEND_DIR),
           "APP_MODE": "local", "DATABASE_URL": empty_database, "GROQ_API_KEY": "gsk_not_used",
           "GROQ_MODEL": "openai/gpt-oss-120b", "JWT_SECRET": secrets.token_urlsafe(48),
           "FRONTEND_URL": "http://localhost:5173", "CORS_ORIGINS": "http://localhost:5173",
           "WORKSPACES_DIR": str(tmp_path / "workspaces"), "PACKAGES_DIR": str(tmp_path / "packages"),
           "PORT": str(port), "LOG_LEVEL": "WARNING", "BCRYPT_ROUNDS": "4", "STREAM_POLL_SECONDS": "0.1",
           "AGENT_TEST_MODE": "fake", "AGENT_FAKE_SCRIPT": str(script), "MAILER_MODE": "file",
           "MAIL_FILE": str(tmp_path / "mail.jsonl"), "DOCKER_HOST": "unix:///nonexistent/docker.sock"}
    server = subprocess.Popen([python, "-m", "app"], cwd=tmp_path, env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}/api/v1"
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert server.poll() is None and time.monotonic() < deadline, "the server did not start"
            time.sleep(0.2)
        with httpx.Client(base_url=base, timeout=10) as http:
            token = http.post("/auth/register", json={"name": "Stop Test", "email": "stop@example.com",
                                                      "password": "Str0ng-pass"}).json()["data"]["token"]
            mail = (tmp_path / "mail.jsonl").read_text().strip().splitlines()[-1]
            link = re.search(r"verify-email\?token=([A-Za-z0-9_-]+)", json.loads(mail)["body"]).group(1)
            http.post("/auth/verify-email", json={"token": link})
            headers = {"Authorization": f"Bearer {token}"}
            project_id = http.post("/projects", headers=headers, json={
                "name": "Shop", "language": "java", "source_type": "local_path",
                "source_reference": str(java_project(tmp_path / "shop", rate="0.05")),
                "test_command": JAVA_COMMAND}).json()["data"]["id"]
            run_id = http.post(f"/projects/{project_id}/runs", headers=headers,
                               json={"bug_description": "The summer sale shows 5% discount, it should be 10%."}
                               ).json()["data"]["id"]
            deadline = time.monotonic() + 30
            while http.get(f"/runs/{run_id}", headers=headers).json()["data"]["status"] != "waiting_for_hint":
                assert time.monotonic() < deadline
                time.sleep(0.2)
            # A Run page: the live stream stays open while the run waits for the user.
            live = httpx.Client(timeout=None).send(httpx.Request("GET", f"{base}/runs/{run_id}/events",
                                                                 headers=headers), stream=True)
            assert live.status_code == 200
            lines = live.iter_lines()
            while "waiting_for_hint" not in next(lines):  # the replay has reached the end
                pass
            stopped_at = time.monotonic()
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=SHUTDOWN_GRACE_SECONDS + 15)
            finally:
                took = time.monotonic() - stopped_at
                live.close()
        print(f"\nFE7 shutdown: the server stopped {took:.1f} s after SIGTERM with a live stream open")
        assert took < SHUTDOWN_GRACE_SECONDS + 10
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
