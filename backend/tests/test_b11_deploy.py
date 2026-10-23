"""Phase B11 (part 5): a fresh machine following only the README (D01), and the migration command on a database
that is one file behind (D02)."""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.load import free_port
from tests.sample_projects import JAVA_COMMAND, java_project

BACKEND_DIR = Path(__file__).resolve().parent.parent
README = BACKEND_DIR.parent / "README.md"
LONG = os.environ.get("SANGRA_LONG_TESTS") == "1"


def readme_backend_steps() -> list[str]:
    """The commands of the README section "Running the backend"."""
    text = README.read_text(encoding="utf-8")
    section = text.split("### Running the backend", 1)[1].split("### ", 1)[0]
    block = section.split("```bash", 1)[1].split("```", 1)[0]
    return [line.split("#", 1)[0].strip() for line in block.strip().splitlines() if line.strip()]


def fresh_checkout(target: Path) -> Path:
    """What a fresh clone holds for the backend (the app, the migrations, the requirements and .env.example)."""
    target.mkdir(parents=True)
    for name in ("app", "migrations"):
        shutil.copytree(BACKEND_DIR / name, target / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("requirements.txt", "requirements-dev.txt", ".env.example"):
        shutil.copy(BACKEND_DIR / name, target / name)
    return target


def write_env(folder: Path, values: dict[str, str]) -> None:
    """`cp .env.example .env`, then put real values in it (the README's second step)."""
    lines = []
    for line in (folder / ".env.example").read_text().splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={values.pop(key)}" if "=" in line and not line.startswith("#") and key in values
                     else line)
    lines += [f"{key}={value}" for key, value in values.items()]
    (folder / ".env").write_text("\n".join(lines) + "\n")


def run(python: str, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.isupper() or k in ("PATH", "HOME", "LANG", "SSL_CERT_FILE",
                                                                              "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                                                                              "PIP_CERT", "REQUESTS_CA_BUNDLE")}
    return subprocess.run([python, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)


@pytest.mark.slow
def test_B11_D01_a_fresh_machine_following_only_the_readme(empty_database, tmp_path):
    steps = readme_backend_steps()
    assert steps == ["cd backend", "python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt",
                     "cp .env.example .env", ".venv/bin/python -m app.migrate", ".venv/bin/python -m app"], steps
    folder = fresh_checkout(tmp_path / "clone" / "backend")
    if LONG:  # step 1 for real: a new virtual environment and every package from the requirements
        subprocess.run([sys.executable, "-m", "venv", str(folder / ".venv")], check=True, timeout=300)
        install = subprocess.run([str(folder / ".venv/bin/pip"), "install", "-q", "-r", "requirements-dev.txt"],
                                 cwd=folder, capture_output=True, text=True, timeout=900)
        assert install.returncode == 0, install.stderr[-2000:]
        python = str(folder / ".venv/bin/python")
    else:
        python = str(BACKEND_DIR / ".venv/bin/python")
    port = free_port()
    project = java_project(tmp_path / "shop", rate="0.05")
    script = tmp_path / "fake_agent.json"
    script.write_text(json.dumps({
        "llm": [{"tool": "search_files", "args": {"query": "discount"}},
                {"answer": {"outcome": "found", "summary": "summerRate returns 0.05", "findings": [
                    {"file_path": "src/main/java/shop/Discount.java", "start_line": 3, "end_line": 6,
                     "note": "the rate"}]}},
                {"answer": {"explanation": "The summer rate must be 0.10.", "edits": [
                    {"file_path": "src/main/java/shop/Discount.java", "start_line": 5, "end_line": 5,
                     "old_code": "        return 0.05;", "new_code": "        return 0.10;", "reason": "rate"}]}}],
        "tests": [{"status": "failed", "exit_code": 1, "log": "1 test failed"},
                  {"status": "passed", "exit_code": 0, "log": "all tests passed"}]}))
    write_env(folder, {
        "DATABASE_URL": empty_database, "JWT_SECRET": secrets.token_urlsafe(48),
        "WORKSPACES_DIR": str(tmp_path / "data" / "workspaces"), "PACKAGES_DIR": str(tmp_path / "data" / "packages"),
        "PORT": str(port), "LOG_LEVEL": "WARNING",
        # The first journey runs without Groq (not reachable here): the scripted agent of Gaps #10.
        "AGENT_TEST_MODE": "fake", "AGENT_FAKE_SCRIPT": str(script)})

    migrated = run(python, ["-m", "app.migrate"], folder)
    assert migrated.returncode == 0, migrated.stderr
    assert "Applied: 001_initial_schema.sql" in migrated.stdout

    server = subprocess.Popen([python, "-m", "app"], cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, env={k: v for k, v in os.environ.items() if not k.isupper()
                                              or k in ("PATH", "HOME", "LANG")})
    output: list[str] = []
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
            token = http.post("/auth/register", json={"name": "First User", "email": "first@example.com",
                                                      "password": "Str0ng-pass"}).json()["data"]["token"]
            headers = {"Authorization": f"Bearer {token}"}
            link_token = None
            deadline = time.monotonic() + 10
            while link_token is None and time.monotonic() < deadline:  # the console mailer prints the email
                output.append(server.stdout.readline())
                found = re.search(r"/verify-email\?token=([A-Za-z0-9_-]+)", output[-1])
                link_token = found.group(1) if found else None
            assert link_token, "the verification email was not printed"
            assert http.post("/auth/verify-email", json={"token": link_token}).status_code == 200
            project_id = http.post("/projects", headers=headers, json={
                "name": "Shop", "language": "java", "source_type": "local_path", "source_reference": str(project),
                "test_command": JAVA_COMMAND}).json()["data"]["id"]
            run_id = http.post(f"/projects/{project_id}/runs", headers=headers,
                               json={"bug_description": "The summer sale shows 5% discount, it should be 10%."}
                               ).json()["data"]["id"]

            def wait(status: str) -> dict:
                deadline = time.monotonic() + 30
                while True:
                    detail = http.get(f"/runs/{run_id}", headers=headers).json()["data"]
                    if detail["status"] in (status, "failed", "stopped"):
                        return detail
                    assert time.monotonic() < deadline, detail["status"]
                    time.sleep(0.2)

            waiting = wait("waiting_for_approval")
            assert waiting["status"] == "waiting_for_approval", waiting
            time.sleep(0.5)
            http.post(f"/runs/{run_id}/approval", headers=headers,
                      json={"attempt_id": waiting["attempts"][-1]["id"], "decision": "accepted"})
            done = wait("succeeded")
            assert done["status"] == "succeeded", done
            package = http.get(f"/runs/{run_id}/package", headers=headers)
            assert package.status_code == 200 and package.content[:2] == b"PK"
    finally:
        server.terminate()
        server.wait(timeout=20)
    print(f"\nB11-D01: fresh folder, {'new venv + pip install, ' if LONG else ''}.env from .env.example, migrate, "
          f"start; first journey (register, verify by the printed link, project, run, approve, download) passed")


def test_B11_D02_the_migration_command_one_file_behind(empty_database, tmp_path):
    folder = tmp_path / "migrations"
    shutil.copytree(BACKEND_DIR / "migrations", folder)
    env_dir = tmp_path / "cwd"
    env_dir.mkdir()
    (env_dir / ".env").write_text(f"DATABASE_URL={empty_database}\n")
    python = str(BACKEND_DIR / ".venv/bin/python")

    def migrate(*args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        env["PYTHONPATH"] = str(BACKEND_DIR)
        return subprocess.run([python, "-m", "app.migrate", "--dir", str(folder), *args], cwd=env_dir, env=env,
                              capture_output=True, text=True, timeout=120)

    first = migrate()
    assert first.returncode == 0 and "Applied: 001_initial_schema.sql" in first.stdout, first.stderr
    (folder / "002_add_notes.sql").write_text("ALTER TABLE projects ADD COLUMN b11_d02_note text;")
    status = migrate("--status")
    assert "Applied: 001_initial_schema.sql" in status.stdout and "Pending: 002_add_notes.sql" in status.stdout
    second = migrate()
    assert second.returncode == 0 and second.stdout.strip() == "Applied: 002_add_notes.sql", second.stdout
    third = migrate()
    assert third.stdout.strip() == "Nothing to apply, the database is up to date."
    print(f"\nB11-D02: one file behind: only 002 was applied; {third.stdout.strip()}")
