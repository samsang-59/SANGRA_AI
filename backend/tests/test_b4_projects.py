"""Phase B4: projects (create, list, get, edit, delete, the 20-project limit)."""

import asyncio
import statistics
import time
from pathlib import Path

import pytest

from tests.factories import insert_package, insert_project, insert_run, user_with_token
from tests.load import p95, raw_burst, running_server

URL = "/api/v1/projects"


def github_project(**overrides) -> dict:
    body = {"name": "Shop", "language": "java", "source_type": "github",
            "source_reference": "https://github.com/owner/repo", "test_command": "mvn -q test"}
    body.update(overrides)
    return body


def assert_error(response, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]["message"]


@pytest.fixture
async def alice(db):
    return await user_with_token(db, name="Alice")


@pytest.fixture
async def bob(db):
    return await user_with_token(db, name="Bob")


async def create(client, user, **overrides):
    return await client.post(URL, json=github_project(**overrides), headers=user["headers"])


# --- Functional ---------------------------------------------------------------------------------------

async def test_B4_F01_create_each_type(client, alice, tmp_path):
    github = await create(client, alice)
    assert github.status_code == 201
    data = github.json()["data"]
    assert data["source_type"] == "github" and data["source_reference"] == "https://github.com/owner/repo"
    assert data["branch"] is None and "user_id" not in data

    zipped = await create(client, alice, source_type="zip", source_reference=None, language="python",
                          test_command="pytest -q")
    assert zipped.status_code == 201 and zipped.json()["data"]["source_reference"] is None

    folder = tmp_path / "my project"
    folder.mkdir()
    local = await create(client, alice, source_type="local_path", source_reference=str(folder), branch="feature/x-1")
    assert local.status_code == 201
    assert local.json()["data"]["source_reference"] == str(folder) and local.json()["data"]["branch"] == "feature/x-1"


async def test_B4_F02_list_newest_first_and_only_mine(client, alice, bob):
    for n in range(3):
        await create(client, alice, name=f"A{n}")
    await create(client, bob, name="B0")
    response = await client.get(URL, headers=alice["headers"])
    assert response.status_code == 200
    data = response.json()["data"]
    assert [p["name"] for p in data["items"]] == ["A2", "A1", "A0"] and data["total"] == 3


async def test_B4_F03_get_one(client, alice):
    project_id = (await create(client, alice)).json()["data"]["id"]
    response = await client.get(f"{URL}/{project_id}", headers=alice["headers"])
    assert response.status_code == 200 and response.json()["data"]["id"] == project_id


async def test_B4_F04_edit_one_field(client, alice, db):
    created = (await create(client, alice, branch="develop")).json()["data"]
    await asyncio.sleep(0.01)
    response = await client.patch(f"{URL}/{created['id']}", json={"name": "Renamed"}, headers=alice["headers"])
    assert response.status_code == 200
    updated = response.json()["data"]
    changed = {k for k in created if created[k] != updated[k]}
    assert changed == {"name", "updated_at"}


async def test_B4_F05_delete_removes_runs_and_cleans_up_after_commit(client, app, alice, db, settings):
    project = (await create(client, alice)).json()["data"]
    run = await insert_run(db, alice["id"], project["id"], status="succeeded")
    package_file = settings.packages_dir / f"{run['id']}.zip"
    package_file.write_bytes(b"zip")
    await insert_package(db, run["id"], storage_path=str(package_file))

    seen = []

    async def thread_deleter(run_id: int) -> None:
        cur = await db.execute("SELECT count(*) AS n FROM runs WHERE id = %s", (run_id,))
        seen.append((run_id, (await cur.fetchone())["n"], package_file.exists()))

    app.state.run_cleanup.thread_deleters.append(thread_deleter)
    response = await client.delete(f"{URL}/{project['id']}", headers=alice["headers"])
    assert response.status_code == 200
    assert seen == [(run["id"], 0, False)]  # called AFTER the commit: the run row is already gone
    assert (await client.get(f"{URL}/{project['id']}", headers=alice["headers"])).status_code == 404


# --- Failure --------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["", "x" * 101, "   "])
async def test_B4_X01_bad_names(client, alice, name):
    assert_error(await create(client, alice, name=name), 422, "VALIDATION_ERROR")


async def test_B4_X02_bad_language_and_source_type(client, alice):
    assert_error(await create(client, alice, language="go"), 422, "VALIDATION_ERROR")
    assert_error(await create(client, alice, source_type="ftp"), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("url", [
    "https://gitlab.com/owner/repo", "http://github.com/owner/repo", "https://github.com/owner",
    "https://github.com/owner/", "https://github.com/owner/repo/tree/main", "https://github.com/owner/repo?x=1",
    "https://github.com/a/b; rm -rf /", "ext::sh -c touch% /tmp/pwned", "-https://github.com/owner/repo",
    "--upload-pack=touch /tmp/x", "https://github.com/-owner/repo", "https://user@github.com/owner/repo",
])
async def test_B4_X03_bad_github_urls(client, alice, url):
    message = assert_error(await create(client, alice, source_reference=url), 422, "VALIDATION_ERROR")
    assert "source_reference" in message


@pytest.mark.parametrize("path", ["relative/folder", "", "/tmp/with\x00null", "/home/x/../etc", "../up"])
async def test_B4_X04_bad_local_paths(client, alice, path):
    assert_error(await create(client, alice, source_type="local_path", source_reference=path), 422, "VALIDATION_ERROR")


async def test_B4_X05_branches(client, alice):
    for bad in ["has space", "-starts-with-dash", "semi;colon", "$(touch x)", "a..b", "ends/", "x.lock", "`x`"]:
        assert_error(await create(client, alice, branch=bad), 422, "VALIDATION_ERROR")
    for good in ["feature/x-1", "main", "release-2.0", "fix_123"]:
        assert (await create(client, alice, branch=good, name=good)).status_code == 201


async def test_B4_X06_test_command_length(client, alice):
    assert_error(await create(client, alice, test_command=""), 422, "VALIDATION_ERROR")
    assert_error(await create(client, alice, test_command="x" * 501), 422, "VALIDATION_ERROR")
    assert (await create(client, alice, test_command="x" * 500)).status_code == 201


async def test_B4_X07_reference_rules(client, alice):
    assert_error(await create(client, alice, source_reference=None), 422, "VALIDATION_ERROR")
    assert_error(await create(client, alice, source_type="local_path", source_reference=None), 422, "VALIDATION_ERROR")
    zipped = await create(client, alice, source_type="zip", source_reference="https://github.com/owner/repo")
    assert zipped.status_code == 201 and zipped.json()["data"]["source_reference"] is None  # ignored for zip


@pytest.mark.settings(APP_MODE="deployed")
async def test_B4_X08_local_path_in_deployed_mode(client, alice, tmp_path):
    response = await create(client, alice, source_type="local_path", source_reference=str(tmp_path))
    assert_error(response, 400, "LOCAL_PATH_NOT_ALLOWED")


async def test_B4_X09_local_path_missing_unreadable_or_a_file(client, alice, tmp_path, monkeypatch):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    for path in [tmp_path / "missing", a_file]:
        assert_error(await create(client, alice, source_type="local_path", source_reference=str(path)), 400,
                     "PATH_NOT_FOUND")
    # Not readable: the tests run as root, which reads everything, so the refusal of the OS is simulated.
    import os

    real_scandir = os.scandir
    locked = tmp_path / "locked"
    locked.mkdir()

    def scandir(path):
        if Path(path) == locked:
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert_error(await create(client, alice, source_type="local_path", source_reference=str(locked)), 400,
                 "PATH_NOT_FOUND")


async def test_B4_X10_edit_that_makes_the_project_invalid(client, alice):
    zipped = (await create(client, alice, source_type="zip", source_reference=None)).json()["data"]
    response = await client.patch(f"{URL}/{zipped['id']}", json={"source_type": "github"}, headers=alice["headers"])
    assert_error(response, 400, "INVALID_PROJECT_CONFIG")
    github = (await create(client, alice)).json()["data"]
    response = await client.patch(f"{URL}/{github['id']}", json={"source_type": "local_path"}, headers=alice["headers"])
    assert_error(response, 400, "INVALID_PROJECT_CONFIG")  # a GitHub URL is not an absolute folder
    response = await client.patch(f"{URL}/{github['id']}", json={"source_reference": None}, headers=alice["headers"])
    assert_error(response, 400, "INVALID_PROJECT_CONFIG")
    # Valid switches work, and a zip project drops its reference.
    ok = await client.patch(f"{URL}/{github['id']}", json={"source_type": "zip"}, headers=alice["headers"])
    assert ok.status_code == 200 and ok.json()["data"]["source_reference"] is None
    for field in ["name", "language", "source_type", "test_command"]:
        assert_error(await client.patch(f"{URL}/{github['id']}", json={field: None}, headers=alice["headers"]), 422,
                     "VALIDATION_ERROR")


async def test_B4_X11_twenty_first_project(client, alice):
    for n in range(20):
        assert (await create(client, alice, name=f"P{n}")).status_code == 201
    assert_error(await create(client, alice, name="P20"), 409, "PROJECT_LIMIT_REACHED")


async def test_B4_X12_delete_with_an_active_run(client, alice, db):
    project = (await create(client, alice)).json()["data"]
    await insert_run(db, alice["id"], project["id"], status="waiting_for_approval")
    assert_error(await client.delete(f"{URL}/{project['id']}", headers=alice["headers"]), 409, "PROJECT_HAS_ACTIVE_RUN")
    cur = await db.execute("SELECT count(*) AS n FROM runs WHERE project_id = %s", (project["id"],))
    assert (await cur.fetchone())["n"] == 1
    assert (await client.get(f"{URL}/{project['id']}", headers=alice["headers"])).status_code == 200


async def test_B4_X13_bad_and_missing_ids(client, alice):
    for bad in ["0", "-1", "abc", "99999999999999999999", "1.5", "9223372036854775808"]:
        for method in ("get", "patch", "delete"):
            kwargs = {"json": {"name": "x"}} if method == "patch" else {}
            response = await getattr(client, method)(f"{URL}/{bad}", headers=alice["headers"], **kwargs)
            assert_error(response, 422, "VALIDATION_ERROR")
    for method in ("get", "patch", "delete"):
        kwargs = {"json": {"name": "x"}} if method == "patch" else {}
        response = await getattr(client, method)(f"{URL}/9223372036854775807", headers=alice["headers"], **kwargs)
        assert_error(response, 404, "NOT_FOUND")


async def test_B4_X14_every_route_needs_a_jwt(client, alice):
    project_id = (await create(client, alice)).json()["data"]["id"]
    for method, path in [("post", URL), ("get", URL), ("get", f"{URL}/{project_id}"),
                         ("patch", f"{URL}/{project_id}"), ("delete", f"{URL}/{project_id}")]:
        kwargs = {"json": github_project()} if method in ("post", "patch") else {}
        assert_error(await getattr(client, method)(path, **kwargs), 401, "UNAUTHORIZED")


async def test_B4_X15_file_cleanup_fails_after_the_commit(client, app, alice, db, settings, log_output):
    project = (await create(client, alice)).json()["data"]
    run = await insert_run(db, alice["id"], project["id"], status="failed")
    await insert_package(db, run["id"], storage_path="/etc/passwd")  # outside the packages folder: refused

    async def failing_deleter(run_id: int) -> None:
        raise RuntimeError("langgraph store down")

    app.state.run_cleanup.thread_deleters.append(failing_deleter)
    response = await client.delete(f"{URL}/{project['id']}", headers=alice["headers"])
    assert response.status_code == 200
    assert Path("/etc/passwd").exists()
    cur = await db.execute("SELECT count(*) AS n FROM projects WHERE id = %s", (project["id"],))
    assert (await cur.fetchone())["n"] == 0
    logs = log_output.getvalue()
    assert "Could not delete a package file" in logs and "Could not delete the LangGraph data" in logs


# --- Security -------------------------------------------------------------------------------------

async def test_B4_S01_S04_other_users_get_identical_404s(client, alice, bob):
    project = (await create(client, alice)).json()["data"]
    not_mine = [
        await client.get(f"{URL}/{project['id']}", headers=bob["headers"]),
        await client.patch(f"{URL}/{project['id']}", json={"name": "Hacked"}, headers=bob["headers"]),
        await client.delete(f"{URL}/{project['id']}", headers=bob["headers"]),
    ]
    missing = [
        await client.get(f"{URL}/999999", headers=bob["headers"]),
        await client.patch(f"{URL}/999999", json={"name": "Hacked"}, headers=bob["headers"]),
        await client.delete(f"{URL}/999999", headers=bob["headers"]),
    ]
    for a, b in zip(not_mine, missing):
        assert a.status_code == b.status_code == 404
        assert a.json() == b.json()
    unchanged = (await client.get(f"{URL}/{project['id']}", headers=alice["headers"])).json()["data"]
    assert unchanged == project


async def test_B4_S02_list_never_shows_other_users_projects(client, alice, bob):
    await create(client, alice, name="Secret")
    assert (await client.get(URL, headers=bob["headers"])).json()["data"] == {"items": [], "total": 0}


async def test_B4_S03_patch_ignores_protected_fields(client, alice, bob, db):
    project = (await create(client, alice)).json()["data"]
    response = await client.patch(f"{URL}/{project['id']}", headers=alice["headers"],
                                  json={"user_id": bob["id"], "id": 12345, "created_at": "2000-01-01T00:00:00Z"})
    assert response.status_code == 200
    cur = await db.execute("SELECT id, user_id, created_at FROM projects WHERE id = %s", (project["id"],))
    row = await cur.fetchone()
    assert row["user_id"] == alice["id"] and row["id"] == project["id"]
    assert row["created_at"].isoformat() == project["created_at"].replace("Z", "+00:00")


async def test_B4_S05_script_and_sql_text_in_project_fields(client, alice, db):
    name = "<script>alert(1)</script> '; DROP TABLE projects; --"
    response = await create(client, alice, name=name, test_command="mvn test; echo '$(whoami)'")
    assert response.status_code == 201 and response.json()["data"]["name"] == name
    assert (await client.get(URL, headers=alice["headers"])).json()["data"]["items"][0]["name"] == name


# --- Concurrency ----------------------------------------------------------------------------------

async def test_B4_C01_30_parallel_creates_at_19_projects(client, alice, db):
    for n in range(19):
        await insert_project(db, alice["id"], name=f"P{n}")
    responses = await asyncio.gather(*(create(client, alice, name=f"New{n}") for n in range(30)))
    statuses = [r.status_code for r in responses]
    assert statuses.count(201) == 1 and statuses.count(409) == 29
    assert (await client.get(URL, headers=alice["headers"])).json()["data"]["total"] == 20


async def test_B4_C02_two_parallel_edits(client, alice):
    project = (await create(client, alice)).json()["data"]
    edits = [{"name": "First", "test_command": "one"}, {"name": "Second", "test_command": "two"}]
    responses = await asyncio.gather(*(
        client.patch(f"{URL}/{project['id']}", json=edit, headers=alice["headers"]) for edit in edits))
    assert [r.status_code for r in responses] == [200, 200]
    final = (await client.get(f"{URL}/{project['id']}", headers=alice["headers"])).json()["data"]
    assert (final["name"], final["test_command"]) in {("First", "one"), ("Second", "two")}  # never a mix


async def test_B4_C03_parallel_delete_and_edit(client, alice):
    for _ in range(20):
        project = (await create(client, alice)).json()["data"]
        deleted, edited = await asyncio.gather(
            client.delete(f"{URL}/{project['id']}", headers=alice["headers"]),
            client.patch(f"{URL}/{project['id']}", json={"name": "Edit"}, headers=alice["headers"]),
        )
        assert deleted.status_code == 200
        # The edit either ran first (200) or found the project gone (404); never a 500.
        assert edited.status_code in (200, 404)
        assert (await client.get(f"{URL}/{project['id']}", headers=alice["headers"])).status_code == 404


# --- Load -----------------------------------------------------------------------------------------

@pytest.mark.slow
async def test_B4_L01_list_of_20_projects(client, alice, db):
    for n in range(20):
        await insert_project(db, alice["id"], name=f"P{n}")
    durations = []
    for _ in range(300):
        started = time.perf_counter()
        response = await client.get(URL, headers=alice["headers"])
        durations.append((time.perf_counter() - started) * 1000)
        assert len(response.json()["data"]["items"]) == 20
    durations.sort()
    print(f"\nB4-L01: list of 20 projects p50 {statistics.median(durations):.2f} ms, p95 {p95(durations):.2f} ms")
    assert p95(durations) < 50


@pytest.mark.slow
async def test_B4_L02_100_users_500_parallel_lists(db, base_env, tmp_path):
    users = [await user_with_token(db) for _ in range(100)]
    async with db.cursor() as cur:
        await cur.executemany(
            "INSERT INTO projects (user_id, name, language, source_type, source_reference, test_command) "
            "SELECT %s, 'P' || g, 'java', 'github', 'https://github.com/o/r', 'mvn test' FROM generate_series(1, 20) g",
            [(u["id"],) for u in users])
    requests = [(URL, {"Authorization": users[n % 100]["headers"]["Authorization"]}) for n in range(500)]
    with running_server(base_env, tmp_path) as port:
        await raw_burst(port, requests)  # warm-up
        statuses, durations = await raw_burst(port, requests)
    print(f"\nB4-L02: 500 parallel project lists (100 users x 20): p50 {statistics.median(durations):.0f} ms, "
          f"p95 {p95(durations):.0f} ms")
    assert statuses == [200] * 500
    assert p95(durations) < 300
