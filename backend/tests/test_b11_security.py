"""Phase B11 (part 2): the security sweep over the whole API (B11-S01..S07)."""

import asyncio
import io
import json
import re
import subprocess
import zipfile

import pytest

from app.repositories.event_repository import FINAL_STATUSES
from tests.agent_helpers import wait_for
from tests.conftest import BACKEND_DIR, KNOWN_SECRETS, FakeMailer
from tests.factories import insert_attempt, insert_edit, insert_package, insert_project, insert_run, user_with_token
from tests.test_b5_runs import install

PUBLIC = {("GET", "/api/v1/health"), ("POST", "/api/v1/auth/register"), ("POST", "/api/v1/auth/login"),
          ("POST", "/api/v1/auth/verify-email"), ("POST", "/api/v1/auth/forgot-password"),
          ("POST", "/api/v1/auth/reset-password")}
INTERNAL_DETAIL = re.compile(r"Traceback|psycopg|File \\?\"|\.py\b|syntax error at|\bline \d+|pydantic\.|starlette\.|"
                             r"asyncio\.|NUL character|DETAIL:")


def every_route(app) -> list[tuple[str, str]]:
    """Every (method, path) the app serves, from its own route table (the OpenAPI description)."""
    return [(method.upper(), path) for path, methods in app.openapi()["paths"].items() for method in methods]


def fill(path: str, **ids) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), 1)), path)


# --- S01, S02 -----------------------------------------------------------------------------------------------

async def test_B11_S01_every_protected_route_needs_a_jwt(app, client):
    routes = every_route(app)
    assert len(routes) == 24 and PUBLIC <= set(routes)
    for method, path in routes:
        for headers in ({}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic abc"}):
            response = await client.request(method, fill(path), headers=headers)
            if (method, path) in PUBLIC:
                assert response.status_code != 401, (method, path)
            else:
                assert response.status_code == 401, (method, path, headers, response.text)
                assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_B11_S02_user_b_with_user_a_ids_gets_404_everywhere(app, client, db, tmp_path):
    install(app, passive=True)
    alice = await user_with_token(db, is_email_verified=True)
    bob = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, alice["id"], source_type="local_path", source_reference=str(tmp_path))
    run = await insert_run(db, alice["id"], project["id"], status="waiting_for_approval")
    attempt = await insert_attempt(db, run["id"], status="waiting_for_approval")
    await insert_edit(db, attempt["id"])
    done = await insert_run(db, alice["id"], project["id"], status="succeeded", source_type="local_path",
                            source_reference=str(tmp_path))
    await insert_package(db, done["id"])
    bodies = {
        ("PATCH", "/api/v1/projects/{project_id}"): {"json": {"name": "Taken over"}},
        ("POST", "/api/v1/projects/{project_id}/runs"): {"json": {"bug_description": "A bug that is long enough"}},
        ("POST", "/api/v1/runs/{run_id}/approval"): {"json": {"attempt_id": attempt["id"], "decision": "accepted"}},
        ("POST", "/api/v1/runs/{run_id}/hint"): {"json": {"text": "look at the discount"}},
    }
    # Routes acting on a finished run use the finished one, the others the waiting one.
    on_done = {("GET", "/api/v1/runs/{run_id}/package"), ("POST", "/api/v1/runs/{run_id}/apply-to-source"),
               ("DELETE", "/api/v1/runs/{run_id}")}
    with_ids = [(m, p) for m, p in every_route(app) if "{" in p]
    assert len(with_ids) == 12
    for method, path in with_ids:
        run_id = done["id"] if (method, path) in on_done else run["id"]
        url = fill(path, project_id=project["id"], run_id=run_id)
        response = await client.request(method, url, headers=bob["headers"], **bodies.get((method, path), {}))
        assert response.status_code == 404, (method, path, response.text)
        missing = await client.request(method, fill(path, project_id=99999999, run_id=99999999),
                                       headers=bob["headers"], **bodies.get((method, path), {}))
        assert missing.json() == response.json()  # "not yours" and "does not exist" look the same
    cur = await db.execute("SELECT status, reject_count FROM runs WHERE id = %s", (run["id"],))
    assert dict(await cur.fetchone()) == {"status": "waiting_for_approval", "reject_count": 0}
    cur = await db.execute("SELECT name FROM projects WHERE id = %s", (project["id"],))
    assert (await cur.fetchone())["name"] == "Shop"
    cur = await db.execute("SELECT count(*) AS n FROM runs WHERE user_id = %s", (alice["id"],))
    assert (await cur.fetchone())["n"] == 2


# --- S03 ------------------------------------------------------------------------------------------------------

FUZZ = [
    "'; DROP TABLE users; --", "\" OR 1=1 --", "<script>alert(1)</script>", "{{7*7}}${7*7}", "%s%s%s%n",
    "../../etc/passwd", "a" * 10_000, "naïve café 数学 العربية 🐛🔥", "‮evil‬", "null\x00byte", "\x00",
    "line\r\nbreak", "   ", "", "-rf", "$(touch /tmp/sangra-fuzz)", "`id`", "\U0010ffff",
]


def check_clean(response, what: str) -> None:
    assert response.status_code < 500, f"{what}: {response.status_code} {response.text[:300]}"
    body = response.text
    assert not INTERNAL_DETAIL.search(body), f"{what}: internal detail in {body[:300]}"
    if response.status_code >= 400:
        assert set(response.json()) == {"error"} and set(response.json()["error"]) == {"code", "message"}


@pytest.mark.settings(RATE_LIMIT_LOGIN="100000", RATE_LIMIT_REGISTER="100000", RATE_LIMIT_FORGOT_PASSWORD="100000",
                      RATE_LIMIT_CHANGE_PASSWORD="100000")  # the fuzz is about input, not about rate limits
async def test_B11_S03_fuzz_every_text_field(app, client, db, tmp_path, log_output):
    install(app, passive=True)
    app.state.mailer = FakeMailer()
    user = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"], source_type="local_path", source_reference=str(tmp_path))
    run = await insert_run(db, user["id"], project["id"], status="waiting_for_hint")
    good_project = {"name": "Shop", "language": "java", "source_type": "github",
                    "source_reference": "https://github.com/owner/repo", "test_command": "mvn -q test"}
    stored_names = []
    calls = 0
    for value in FUZZ:
        requests = [
            ("POST", "/auth/register", {"name": value, "email": "fuzz@example.com", "password": "Str0ng-pass"}, None),
            ("POST", "/auth/register", {"name": "Fuzz", "email": value, "password": "Str0ng-pass"}, None),
            ("POST", "/auth/register", {"name": "Fuzz", "email": "fuzz2@example.com", "password": value}, None),
            ("POST", "/auth/login", {"email": value, "password": value}, None),
            ("POST", "/auth/verify-email", {"token": value}, None),
            ("POST", "/auth/forgot-password", {"email": value}, None),
            ("POST", "/auth/reset-password", {"token": value, "new_password": value}, None),
            ("POST", "/auth/change-password", {"old_password": value, "new_password": value}, user["headers"]),
            *[("POST", "/projects", {**good_project, field: value}, user["headers"])
              for field in ("name", "source_reference", "branch", "test_command", "language", "source_type")],
            ("PATCH", f"/projects/{project['id']}", {"name": value}, user["headers"]),
            ("PATCH", f"/projects/{project['id']}", {"test_command": value}, user["headers"]),
            ("POST", f"/runs/{run['id']}/hint", {"text": value}, user["headers"]),
            ("POST", f"/runs/{run['id']}/approval", {"attempt_id": 1, "decision": value, "feedback": value},
             user["headers"]),
        ]
        for method, path, body, headers in requests:
            response = await client.request(method, f"/api/v1{path}", json=body, headers=headers or {})
            check_clean(response, f"{method} {path} {json.dumps(body)[:80]}")
            calls += 1
            if path == "/projects" and response.status_code == 201 and body["name"] == value:
                stored_names.append((response.json()["data"]["id"], value))
        for name in ("status", "page", "project_id", "limit"):
            check_clean(await client.get("/api/v1/runs", params={name: value}, headers=user["headers"]), name)
        other = await client.post(f"/api/v1/projects/{project['id']}/runs", json={"bug_description": value},
                                  headers=user["headers"])
        check_clean(other, f"start run {value[:20]!r}")
        calls += 4
    for bad_id in ("0", "-1", "abc", "1e5", "99999999999999999999", "%00", "1;DROP", "%27%20OR%201=1"):
        check_clean(await client.get(f"/api/v1/runs/{bad_id}", headers=user["headers"]), bad_id)
        check_clean(await client.get(f"/api/v1/projects/{bad_id}", headers=user["headers"]), bad_id)
    # Stored as plain text, exactly as sent; the database is intact.
    for project_id, value in stored_names:
        cur = await db.execute("SELECT name FROM projects WHERE id = %s", (project_id,))
        assert (await cur.fetchone())["name"] == value.strip()
    cur = await db.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public' "
                           "AND table_name = ANY(%s)", (["users", "projects", "runs", "attempts", "file_edits",
                                                         "run_events", "test_results", "packages", "user_tokens"],))
    assert (await cur.fetchone())["n"] == 9
    assert "Unhandled error" not in log_output.getvalue()
    print(f"\nB11-S03: {calls} fuzz requests, {len(FUZZ)} values, no 500, {len(stored_names)} odd names stored as text")


# --- S04 ------------------------------------------------------------------------------------------------------

async def test_B11_S04_no_secret_or_raw_token_anywhere(app, client, db, tmp_path, log_output):
    install(app, passive=False)
    mailer = FakeMailer()
    app.state.mailer = mailer
    seen: list[str] = []

    async def keep(response):
        await response.aread()
        seen.append(response.text + json.dumps(dict(response.headers)))

    client.event_hooks["response"].append(keep)
    email = "secret.check@example.com"
    token = (await client.post("/api/v1/auth/register", json={"name": "Sec", "email": email,
                                                              "password": "Str0ng-pass"})).json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/api/v1/auth/verify-email", json={"token": mailer.tokens(email, "verify-email")[0]})
    await client.post("/api/v1/auth/forgot-password", json={"email": email})
    await app.state.background.drain()  # the reset email is sent after the answer
    await client.post("/api/v1/auth/reset-password", json={"token": mailer.tokens(email, "reset-password")[0],
                                                           "new_password": "N3w-pass-word"})
    await client.post("/api/v1/auth/forgot-password", json={"email": email})  # one reset token stays unused
    await app.state.background.drain()
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "N3w-pass-word"})
    headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    project = await client.post("/api/v1/projects", headers=headers, json={
        "name": "Shop", "language": "java", "source_type": "local_path", "source_reference": str(tmp_path),
        "test_command": "mvn -q test"})
    started = await client.post(f"/api/v1/projects/{project.json()['data']['id']}/runs", headers=headers,
                                json={"bug_description": "The summer sale shows 5% discount, not 10%."})
    run_id = started.json()["data"]["id"]
    await wait_for(db, run_id, ("waiting_for_approval", *FINAL_STATUSES))
    await client.post(f"/api/v1/runs/{run_id}/cancel", headers=headers)
    await wait_for(db, run_id, FINAL_STATUSES)
    await client.get(f"/api/v1/runs/{run_id}", headers=headers)
    await client.get("/api/v1/runs", headers=headers)

    raw_tokens = mailer.tokens(email)
    assert len(raw_tokens) == 3
    cur = await db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    tables = [r["table_name"] for r in await cur.fetchall()]
    dump = []
    for table in tables:  # test helper: the names come from the catalog
        cur = await db.execute(f"SELECT row_to_json(t)::text AS row FROM {table} t")
        dump += [r["row"] for r in await cur.fetchall()]
    places = {"logs": log_output.getvalue(), "database": "\n".join(dump), "responses": "\n".join(seen)}
    for where, text in places.items():
        for secret in [*KNOWN_SECRETS, *raw_tokens]:
            assert secret not in text, f"a secret or raw email token was found in the {where}"
    print(f"\nB11-S04: searched {len(seen)} responses, {len(dump)} database rows in {len(tables)} tables, "
          f"{len(places['logs'])} characters of logs: nothing found")


# --- S05 ------------------------------------------------------------------------------------------------------

@pytest.mark.slow
def test_B11_S05_dependency_audit():
    audit = BACKEND_DIR / ".venv" / "bin" / "pip-audit"
    if not audit.exists():
        pytest.skip("pip-audit is not installed (pip install pip-audit)")
    for requirements in ("requirements.txt", "requirements-dev.txt"):
        result = subprocess.run([str(audit), "-r", requirements, "--format", "json"], cwd=BACKEND_DIR,
                                capture_output=True, text=True, timeout=600)
        if result.returncode not in (0, 1) or not result.stdout.strip():
            pytest.skip(f"pip-audit could not reach the vulnerability database: {result.stderr[-200:]}")
        report = json.loads(result.stdout)
        found = [(d["name"], d["version"], v["id"]) for d in report["dependencies"] for v in d.get("vulns", [])]
        print(f"\nB11-S05: {requirements}: {len(report['dependencies'])} packages, known issues: {found or 'none'}")
        assert found == [], "a known issue needs a fix or a written decision in the README"


# --- S06 ------------------------------------------------------------------------------------------------------

async def test_B11_S06_rate_limits(app, client, db):
    app.state.mailer = FakeMailer()
    codes = [(await client.post("/api/v1/auth/register", json={"name": "R", "email": f"r{n}@example.com",
                                                               "password": "Str0ng-pass"})).status_code
             for n in range(6)]
    assert codes == [201] * 5 + [429]
    codes = [(await client.post("/api/v1/auth/login", json={"email": "r0@example.com", "password": "Wrong-pass1"}))
             .status_code for _ in range(11)]
    assert codes == [401] * 10 + [429]
    codes = [(await client.post("/api/v1/auth/forgot-password", json={"email": "r1@example.com"})).status_code
             for _ in range(4)]
    assert codes == [200] * 3 + [429]
    user = await user_with_token(db)
    first = await client.post("/api/v1/auth/resend-verification", headers=user["headers"])
    second = await client.post("/api/v1/auth/resend-verification", headers=user["headers"])
    assert (first.status_code, second.status_code) == (200, 429)
    assert second.json()["error"]["code"] in ("TOO_SOON", "RATE_LIMITED")
    codes = [(await client.post("/api/v1/auth/change-password", headers=user["headers"],
                                json={"old_password": "Wrong-pass1", "new_password": "N3w-pass-word"})).status_code
             for _ in range(6)]
    assert codes == [400] * 5 + [429]
    limited = await client.post("/api/v1/auth/change-password", headers=user["headers"],
                                json={"old_password": "Wrong-pass1", "new_password": "N3w-pass-word"})
    assert limited.json()["error"]["code"] == "RATE_LIMITED" and int(limited.headers["retry-after"]) > 0


# --- S07 ------------------------------------------------------------------------------------------------------

def zip_of(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.slow
async def test_B11_S07_upload_limits(app, client, db):
    from tests.agent_helpers import install_agent

    install_agent(app, [], [])
    user = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"], source_type="zip", source_reference=None)
    url = f"/api/v1/projects/{project['id']}/runs"
    form = {"bug_description": "The summer sale shows 5% discount, not 10%."}

    big = await client.post(url, headers=user["headers"], data=form,
                            files={"zip_file": ("big.zip", b"PK" + b"\0" * (51 * 1024 * 1024), "application/zip")})
    assert big.status_code in (413, 422) and big.json()["error"]["code"] in ("PAYLOAD_TOO_LARGE", "VALIDATION_ERROR")
    cur = await db.execute("SELECT count(*) AS n FROM runs WHERE user_id = %s", (user["id"],))
    assert (await cur.fetchone())["n"] == 0  # refused before a run was created

    many = zip_of((f"f{n}.txt", b"") for n in range(100_000))
    bomb = zip_of([("bomb.txt", b"\0" * (600 * 1024 * 1024))])
    print(f"\nB11-S07: 100k-file zip {len(many) / 1024 / 1024:.1f} MB, zip bomb {len(bomb) / 1024:.0f} KB "
          "(600 MB unpacked)")
    for upload, reason in ((many, "more than 50000 files"), (bomb, "unpacks to more than")):
        started = await client.post(url, headers=user["headers"], data=form,
                                    files={"zip_file": ("code.zip", upload, "application/zip")})
        assert started.status_code == 201, started.text
        run_id = started.json()["data"]["id"]
        row = await wait_for(db, run_id, FINAL_STATUSES, timeout=120)
        assert (row["status"], row["stop_reason"]) == ("failed", "clone_failed")
        cur = await db.execute("SELECT message FROM run_events WHERE run_id = %s AND event_type = 'error'", (run_id,))
        assert any(reason in r["message"] for r in await cur.fetchall())
        # The copy is deleted right AFTER the final status is saved (B10 finish order); a copy with 100,000 files
        # takes a moment to delete, so wait for it instead of looking at once.
        copy = app.state.settings.workspaces_dir / str(user["id"]) / str(run_id)
        deadline = asyncio.get_running_loop().time() + 30
        while copy.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
        assert not copy.exists()
