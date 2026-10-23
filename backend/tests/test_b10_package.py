"""Phase B10: the finish (package, final status, delete the copy), download, apply to my folder, cleanup jobs and the
delete flows."""

import asyncio
import errno
import io
import os
import stat
import time
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager

from app.main import create_app
from app.runners.finalizer import PackageInput, build_package, safe_name
from app.services.cleanup_service import RunCleanup
from app.services.run_progress import STOP_REASON_TEXT, summary_for
from tests.agent_helpers import (
    FILE,
    attempts,
    decide,
    events,
    fix,
    install_agent,
    install_responder,
    investigation,
    run_row,
    start_run,
    user_and_project,
    wait_for,
    wait_idle,
)
from tests.conftest import KNOWN_SECRETS, TEST_GROQ_KEY, make_client
from tests.factories import (
    insert_attempt,
    insert_edit,
    insert_package,
    insert_project,
    insert_run,
    insert_token,
    user_with_token,
)
from tests.fake_llm import answer
from tests.load import running_server

FINAL = ("succeeded", "failed", "stopped")
DAY = timedelta(days=1)
pytestmark = pytest.mark.settings(LLM_RETRY_BASE_SECONDS="0")


def assert_error(response, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]["message"]


async def finished_run(app, client, db, tmp_path, ending: str = "succeeded", name: str = "shop", folder_setup=None):
    """A real run with the LangGraph agent (FakeLLM, FakeTestRunner) that ends as asked:
    succeeded (accept, tests pass), failed (3 accepted fixes whose tests fail) or stopped (cancel at approval)."""
    if ending == "failed":
        script, tests = [*investigation(), answer(fix("0.11")), answer(fix("0.12")), answer(fix("0.13"))], ["failed"] * 4
    else:
        script, tests = [*investigation(), answer(fix())], ["failed", "passed"]
    install_agent(app, script, tests)
    user, project, folder = await user_and_project(db, tmp_path, name)
    if folder_setup:
        folder_setup(folder)
    run_id = await start_run(client, user, project)
    for _ in range(3 if ending == "failed" else 1):
        await wait_for(db, run_id, "waiting_for_approval")
        await wait_idle(app, run_id)
        if ending == "stopped":
            assert (await client.post(f"/api/v1/runs/{run_id}/cancel", headers=user["headers"])).status_code == 200
            break
        assert (await decide(client, user, run_id, "accepted")).status_code == 200
        await wait_for(db, run_id, ("searching", "waiting_for_approval", *FINAL))
    await wait_for(db, run_id, FINAL)
    await wait_idle(app, run_id)
    return user, project, folder, run_id


async def download(client, user, run_id):
    return await client.get(f"/api/v1/runs/{run_id}/package", headers=user["headers"])


async def apply(client, user, run_id):
    return await client.post(f"/api/v1/runs/{run_id}/apply-to-source", headers=user["headers"])


def unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


async def package_rows(db, run_id: int) -> list[dict]:
    cur = await db.execute("SELECT * FROM packages WHERE run_id = %s", (run_id,))
    return await cur.fetchall()


async def expire_package(db, run_id: int) -> None:
    await db.execute("UPDATE packages SET created_at = now() - interval '8 days', expires_at = now() - interval "
                     "'1 day' WHERE run_id = %s", (run_id,))


def make_old(path: Path, days: float = 2) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


# --- Functional ---------------------------------------------------------------------------------------

async def test_B10_F01_package_of_a_succeeded_run(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    assert (await run_row(db, run_id))["status"] == "succeeded"
    response = await download(client, user, run_id)
    assert response.status_code == 200
    files = unzip(response.content)
    assert sorted(files) == ["changes.patch", f"fixed_files/{FILE}", "logs/attempt_1.log", "logs/baseline.log",
                             "summary.md"]
    patch = files["changes.patch"].decode()
    assert f"a/{FILE}" in patch and "-        return 0.05;" in patch and "+        return 0.10;" in patch
    assert files[f"fixed_files/{FILE}"].decode() == (folder / FILE).read_text().replace("0.05", "0.10")
    assert b"summerSaleIsTenPercent" in files["logs/baseline.log"]
    assert b"Failures: 0" in files["logs/attempt_1.log"]


@pytest.mark.parametrize("ending", ["failed", "stopped"])
async def test_B10_F02_package_of_a_failed_and_a_cancelled_run(app, client, db, tmp_path, ending):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path, ending)
    row = await run_row(db, run_id)
    assert row["status"] == ending
    files = unzip((await download(client, user, run_id)).content)
    expected_logs = ["logs/attempt_1.log", "logs/attempt_2.log", "logs/attempt_3.log"] if ending == "failed" else []
    assert sorted(files) == sorted(["attempts.md", "summary.md", "logs/baseline.log", *expected_logs])
    tried = files["attempts.md"].decode()
    if ending == "failed":
        for n, rate in ((1, "0.11"), (2, "0.12"), (3, "0.13")):
            assert f"## Attempt {n}" in tried and f"return {rate};" in tried
        assert "Result: tests_failed (undone)" in tried
    else:
        assert "## Attempt 1" in tried and "return 0.10;" in tried and "Decision: no answer" in tried
    assert "0.05" in (folder / FILE).read_text()


async def test_B10_F03_download_name_size_and_expiry(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    response = await download(client, user, run_id)
    [package] = await package_rows(db, run_id)
    assert response.headers["content-disposition"] == f'attachment; filename="sangra-run-{run_id}.zip"'
    assert response.headers["content-type"] == "application/zip"
    assert int(response.headers["content-length"]) == len(response.content) == package["size_bytes"]
    assert package["expires_at"] - package["created_at"] == timedelta(days=7)
    detail = (await client.get(f"/api/v1/runs/{run_id}", headers=user["headers"])).json()["data"]
    assert detail["package"]["available"] is True and detail["package"]["size_bytes"] == package["size_bytes"]


async def test_B10_F04_apply_to_my_folder(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    before = (folder / FILE).read_text()
    response = await apply(client, user, run_id)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["applied_to_source_at"] is not None
    assert (folder / FILE).read_text() == before.replace("return 0.05;", "return 0.10;")
    assert (await run_row(db, run_id))["applied_to_source_at"] is not None


@pytest.mark.parametrize("ending", ["succeeded", "failed"])
async def test_B10_F05_the_final_summary(app, client, db, tmp_path, ending):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path, ending)
    row = await run_row(db, run_id)
    assert row["final_summary"] == summary_for(ending, row["stop_reason"])
    summary = unzip((await download(client, user, run_id)).content)["summary.md"].decode()
    assert f"Status: **{ending}**" in summary and row["final_summary"] in summary
    assert "Before any change (baseline): failed" in summary
    if ending == "succeeded":
        assert "After attempt 1: passed" in summary and "decision: accepted; result: tests_passed" in summary
    else:
        assert STOP_REASON_TEXT["tests_failed_3_times"] in summary
        assert [f"After attempt {n}: failed" in summary for n in (1, 2, 3)] == [True] * 3


async def _cleanup_scene(db, settings):
    """Rows and files for the cleanup jobs: what must go and what must stay."""
    user = await user_with_token(db, is_email_verified=True)
    other = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"])
    pkg_dir = settings.packages_dir / str(user["id"])
    pkg_dir.mkdir(parents=True, exist_ok=True)
    scene = {"user": user}

    expired_run = await insert_run(db, user["id"], project["id"], status="succeeded")
    scene["expired_file"] = pkg_dir / f"run_{expired_run['id']}.zip"
    scene["expired_file"].write_bytes(b"old package")
    scene["expired"] = await insert_package(db, expired_run["id"], storage_path=str(scene["expired_file"]),
                                            created_at=_ago(days=8), expires_at=_ago(days=1))
    fresh_run = await insert_run(db, user["id"], project["id"], status="failed")
    scene["fresh_file"] = pkg_dir / f"run_{fresh_run['id']}.zip"
    scene["fresh_file"].write_bytes(b"new package")
    make_old(scene["fresh_file"])  # an old file that a live row still points to is never an orphan
    scene["fresh"] = await insert_package(db, fresh_run["id"], storage_path=str(scene["fresh_file"]))

    old_run = await insert_run(db, user["id"], project["id"], status="stopped", created_at=_ago(days=2))
    scene["old_copy"] = settings.workspaces_dir / str(user["id"]) / str(old_run["id"])
    (scene["old_copy"] / "code").mkdir(parents=True)
    await db.execute("UPDATE runs SET workspace_path = %s WHERE id = %s", (str(scene["old_copy"]), old_run["id"]))
    scene["old_run"] = old_run
    young_run = await insert_run(db, user["id"], project["id"], status="failed")
    scene["young_copy"] = settings.workspaces_dir / str(user["id"]) / str(young_run["id"])
    scene["young_copy"].mkdir(parents=True)
    await db.execute("UPDATE runs SET workspace_path = %s WHERE id = %s", (str(scene["young_copy"]), young_run["id"]))
    scene["young_run"] = young_run

    other_project = await insert_project(db, other["id"])
    active = await insert_run(db, other["id"], other_project["id"], status="waiting_for_approval",
                              created_at=_ago(days=2))
    scene["active_copy"] = settings.workspaces_dir / str(other["id"]) / str(active["id"])
    scene["active_copy"].mkdir(parents=True)
    make_old(scene["active_copy"])
    scene["orphan_copy"] = settings.workspaces_dir / str(other["id"]) / "999999"
    scene["orphan_copy"].mkdir(parents=True)
    make_old(scene["orphan_copy"])
    scene["orphan_file"] = pkg_dir / "run_999999.zip"
    scene["orphan_file"].write_bytes(b"left behind")
    make_old(scene["orphan_file"], days=0.1)
    scene["new_orphan_file"] = pkg_dir / ".package-being-built.zip"
    scene["new_orphan_file"].write_bytes(b"being built right now")

    scene["old_token"] = await insert_token(db, user["id"], expires_at=_ago(days=2))
    scene["used_old_token"] = await insert_token(db, user["id"], expires_at=_ago(days=3), used_at=_ago(days=4))
    scene["recent_token"] = await insert_token(db, user["id"], expires_at=_ago(hours=1))
    scene["valid_token"] = await insert_token(db, user["id"])
    return scene


def _ago(**delta):
    from datetime import datetime, timezone
    return datetime.now(timezone.utc) - timedelta(**delta)


async def test_B10_F06_cleanup_jobs(app, db, settings):
    scene = await _cleanup_scene(db, settings)
    results = await app.state.jobs.run_cleanup()
    assert results == {"packages": 1, "workspaces": 2, "orphan_packages": 1, "tokens": 2}

    cur = await db.execute("SELECT id, deleted_at FROM packages ORDER BY id")
    deleted = {r["id"]: r["deleted_at"] for r in await cur.fetchall()}
    assert deleted[scene["expired"]["id"]] is not None and deleted[scene["fresh"]["id"]] is None
    assert not scene["expired_file"].exists() and scene["fresh_file"].exists()
    assert not scene["orphan_file"].exists() and scene["new_orphan_file"].exists()

    assert (await run_row(db, scene["old_run"]["id"]))["workspace_deleted_at"] is not None
    assert (await run_row(db, scene["young_run"]["id"]))["workspace_deleted_at"] is None
    assert not scene["old_copy"].exists() and scene["young_copy"].exists()
    assert scene["active_copy"].exists() and not scene["orphan_copy"].exists()

    cur = await db.execute("SELECT id FROM user_tokens")
    left = {r["id"] for r in await cur.fetchall()}
    assert left == {scene["recent_token"]["id"], scene["valid_token"]["id"]}


async def test_B10_F06_the_jobs_are_scheduled(db, make_settings):
    fast = make_settings({"CLEANUP_INTERVAL_SECONDS": "0.1"})
    user = await user_with_token(db)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="succeeded")
    target = fast.packages_dir / "1" / "x.zip"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    await insert_package(db, run["id"], storage_path=str(target), created_at=_ago(days=8), expires_at=_ago(days=1))
    app = create_app(fast)
    async with LifespanManager(app):
        deadline = time.monotonic() + 5
        while target.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
    assert not target.exists()
    assert (await package_rows(db, run["id"]))[0]["deleted_at"] is not None


# --- Failure -------------------------------------------------------------------------------------------

async def test_B10_X01_building_the_package_fails(app, client, db, tmp_path, monkeypatch, settings, log_output):
    def disk_full(data, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("app.services.package_service.build_package", disk_full)
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    row = await run_row(db, run_id)
    assert (row["status"], row["stop_reason"]) == ("succeeded", None)
    assert row["workspace_deleted_at"] is not None
    run_events = await events(db, run_id)
    assert any(e["event_type"] == "error" and "package could not be built" in e["message"] for e in run_events)
    assert run_events[-1]["event_type"] == "status_changed" and run_events[-1]["details"]["status"] == "succeeded"
    assert await package_rows(db, run_id) == []
    detail = (await client.get(f"/api/v1/runs/{run_id}", headers=user["headers"])).json()["data"]
    assert detail["package"] is None
    assert detail["attempts"][0]["edits"][0]["new_code"] == "        return 0.10;"  # the changes are still shown
    assert_error(await download(client, user, run_id), 409, "RUN_STATE_INVALID")
    assert "could not be built (OSError)" in log_output.getvalue()
    assert list(settings.packages_dir.rglob("*.zip")) == []


async def test_B10_X02_download_before_after_expiry_and_file_gone(app, client, db, tmp_path):
    install_agent(app, [*investigation(), answer(fix())], ["failed"])
    user, project, folder = await user_and_project(db, tmp_path, "early")
    run_id = await start_run(client, user, project)
    await wait_for(db, run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    assert_error(await download(client, user, run_id), 409, "RUN_STATE_INVALID")
    await client.post(f"/api/v1/runs/{run_id}/cancel", headers=user["headers"])
    assert (await download(client, user, run_id)).status_code == 200

    await expire_package(db, run_id)
    assert_error(await download(client, user, run_id), 410, "PACKAGE_EXPIRED")
    detail = (await client.get(f"/api/v1/runs/{run_id}", headers=user["headers"])).json()["data"]
    assert detail["package"]["available"] is False

    user2, project2, folder2, run2 = await finished_run(app, client, db, tmp_path, name="gone")
    Path((await package_rows(db, run2))[0]["storage_path"]).unlink()
    assert_error(await download(client, user2, run2), 410, "PACKAGE_EXPIRED")


async def test_B10_X03_apply_not_allowed(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path, "failed")
    assert_error(await apply(client, user, run_id), 409, "RUN_STATE_INVALID")
    assert "0.05" in (folder / FILE).read_text()

    user2, project2, folder2, run2 = await finished_run(app, client, db, tmp_path, name="twice")
    assert (await apply(client, user2, run2)).status_code == 200
    assert_error(await apply(client, user2, run2), 409, "RUN_STATE_INVALID")
    assert (folder2 / FILE).read_text().count("return 0.10;") == 1

    github_project = await insert_project(db, user["id"])
    github_run = await insert_run(db, user["id"], github_project["id"], status="succeeded")
    assert_error(await apply(client, user, github_run["id"]), 400, "LOCAL_PATH_NOT_ALLOWED")


@pytest.mark.settings(APP_MODE="deployed")
async def test_B10_X03_apply_in_deployed_mode(app, client, db, tmp_path):
    user = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"], source_type="local_path", source_reference=str(tmp_path))
    run = await insert_run(db, user["id"], project["id"], status="succeeded", source_type="local_path",
                           source_reference=str(tmp_path))
    assert_error(await apply(client, user, run["id"]), 400, "LOCAL_PATH_NOT_ALLOWED")


async def test_B10_X04_apply_when_the_package_expired(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    await expire_package(db, run_id)
    assert_error(await apply(client, user, run_id), 410, "PACKAGE_EXPIRED")
    assert "return 0.05;" in (folder / FILE).read_text()
    assert (await run_row(db, run_id))["applied_to_source_at"] is None


async def test_B10_X05_the_user_changed_an_original_line(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    original = (folder / FILE).read_text()
    (folder / FILE).write_text(original.replace("return 0.05;", "return 0.07;"))
    changed = (folder / FILE).read_text()
    message = assert_error(await apply(client, user, run_id), 409, "SOURCE_CHANGED")
    assert "nothing was written" in message
    assert (folder / FILE).read_text() == changed
    assert (await run_row(db, run_id))["applied_to_source_at"] is None
    (folder / FILE).write_text(original)  # put back: applying works again
    assert (await apply(client, user, run_id)).status_code == 200
    assert "return 0.10;" in (folder / FILE).read_text()


async def succeeded_run_with_edits(db, settings, folder: Path, files: dict[str, bytes], edits: list[dict]):
    """A succeeded local-folder run with one passing attempt holding `edits`, and a live package (rows only)."""
    user = await user_with_token(db, is_email_verified=True)
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    project = await insert_project(db, user["id"], source_type="local_path", source_reference=str(folder))
    run = await insert_run(db, user["id"], project["id"], status="succeeded", source_type="local_path",
                           source_reference=str(folder))
    attempt = await insert_attempt(db, run["id"], status="tests_passed", decision="accepted")
    for order, edit in enumerate(edits, start=1):
        await insert_edit(db, attempt["id"], edit_order=order, **edit)
    target = settings.packages_dir / str(user["id"]) / f"run_{run['id']}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"zip")
    await insert_package(db, run["id"], storage_path=str(target))
    return user, run


THREE_FILES = {f"f{n}.txt": f"one\nvalue = {n}\nthree\n".encode() for n in (1, 2, 3)}
THREE_EDITS = [{"file_path": f"f{n}.txt", "start_line": 2, "end_line": 2, "old_code": f"value = {n}",
                "new_code": f"value = {n * 10}"} for n in (1, 2, 3)]


def contents(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


async def test_B10_X06_three_edits_only_the_third_no_longer_matches(app, client, db, tmp_path, settings):
    folder = tmp_path / "three"
    user, run = await succeeded_run_with_edits(db, settings, folder, THREE_FILES, THREE_EDITS)
    (folder / "f3.txt").write_bytes(b"one\nvalue = 333\nthree\n")
    before = contents(folder)
    assert_error(await apply(client, user, run["id"]), 409, "SOURCE_CHANGED")
    assert contents(folder) == before


async def test_B10_X06_all_three_match(app, client, db, tmp_path, settings):
    folder = tmp_path / "three"
    user, run = await succeeded_run_with_edits(db, settings, folder, THREE_FILES, THREE_EDITS)
    assert (await apply(client, user, run["id"])).status_code == 200
    assert contents(folder) == {f"f{n}.txt": f"one\nvalue = {n * 10}\nthree\n".encode() for n in (1, 2, 3)}


async def test_B10_X07_original_deleted_read_only_or_windows_endings(app, client, db, tmp_path, settings):
    folder = tmp_path / "deleted"
    user, run = await succeeded_run_with_edits(db, settings, folder, THREE_FILES, THREE_EDITS)
    (folder / "f2.txt").unlink()
    before = contents(folder)
    assert "does not exist" not in assert_error(await apply(client, user, run["id"]), 409, "SOURCE_CHANGED")
    assert contents(folder) == before

    folder = tmp_path / "readonly"
    user, run = await succeeded_run_with_edits(db, settings, folder, THREE_FILES, THREE_EDITS)
    (folder / "f3.txt").chmod(stat.S_IRUSR | stat.S_IRGRP)
    before = contents(folder)
    assert_error(await apply(client, user, run["id"]), 409, "SOURCE_NOT_WRITABLE")
    assert contents(folder) == before
    assert (await run_row(db, run["id"]))["applied_to_source_at"] is None

    folder = tmp_path / "windows"
    windows = {name: data.replace(b"\n", b"\r\n") for name, data in THREE_FILES.items()}
    user, run = await succeeded_run_with_edits(db, settings, folder, windows, THREE_EDITS)
    assert (await apply(client, user, run["id"])).status_code == 200
    assert contents(folder) == {f"f{n}.txt": f"one\r\nvalue = {n * 10}\r\nthree\r\n".encode() for n in (1, 2, 3)}


async def test_B10_X07_a_write_fails_half_way(app, client, db, tmp_path, settings, monkeypatch):
    from app.agent import edits as edits_module

    folder = tmp_path / "half"
    user, run = await succeeded_run_with_edits(db, settings, folder, THREE_FILES, THREE_EDITS)
    before = contents(folder)
    real_write = edits_module.write_planned
    calls = []

    def failing_write(planned):
        calls.append(planned)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_write(planned)

    monkeypatch.setattr(edits_module, "write_planned", failing_write)
    assert_error(await apply(client, user, run["id"]), 409, "SOURCE_NOT_WRITABLE")
    assert contents(folder) == before  # the first file was put back
    assert not [p for p in folder.iterdir() if p.name.startswith(".sangra-")]


async def test_B10_X08_a_package_file_cannot_be_deleted(app, db, settings, monkeypatch, log_output):
    scene = await _cleanup_scene(db, settings)
    second = scene["fresh"]
    await db.execute("UPDATE packages SET created_at = now() - interval '8 days', expires_at = now() - interval "
                     "'1 day' WHERE id = %s", (second["id"],))
    real_delete = RunCleanup.delete_package_file

    def failing_delete(self, path):
        if path == str(scene["expired_file"]):
            raise PermissionError(errno.EACCES, "Permission denied")
        real_delete(self, path)

    monkeypatch.setattr(RunCleanup, "delete_package_file", failing_delete)
    results = await app.state.jobs.run_cleanup()
    assert results["packages"] == 1 and results["tokens"] == 2  # the job went on, so did the others
    assert scene["expired_file"].exists() and not scene["fresh_file"].exists()
    cur = await db.execute("SELECT id, deleted_at FROM packages")
    deleted = {r["id"]: r["deleted_at"] for r in await cur.fetchall()}
    assert deleted[scene["expired"]["id"]] is None and deleted[second["id"]] is not None
    assert f"the file of package {scene['expired']['id']} could not be deleted (PermissionError)" in log_output.getvalue()

    monkeypatch.setattr(RunCleanup, "delete_package_file", real_delete)
    assert (await app.state.jobs.run_cleanup())["packages"] == 1  # tried again next time
    assert not scene["expired_file"].exists()


async def test_B10_X09_deleting_a_run_when_the_file_delete_fails(app, client, db, tmp_path, monkeypatch, settings):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    package_file = Path((await package_rows(db, run_id))[0]["storage_path"])
    leftover_copy = settings.workspaces_dir / str(user["id"]) / str(run_id)
    leftover_copy.mkdir(parents=True)  # as if deleting the copy had failed too

    def failing_delete(self, path):
        raise PermissionError(errno.EACCES, "Permission denied")

    real_delete = RunCleanup.delete_package_file
    monkeypatch.setattr(RunCleanup, "delete_package_file", failing_delete)
    assert (await client.delete(f"/api/v1/runs/{run_id}", headers=user["headers"])).status_code == 200
    await app.state.background.drain()
    assert await run_row(db, run_id) is None and await package_rows(db, run_id) == []
    assert package_file.exists()

    monkeypatch.setattr(RunCleanup, "delete_package_file", real_delete)
    make_old(package_file)
    make_old(leftover_copy)
    results = await app.state.jobs.run_cleanup()
    assert results["orphan_packages"] == 1 and results["workspaces"] == 1
    assert not package_file.exists() and not leftover_copy.exists()


# --- Security ------------------------------------------------------------------------------------------

def _with_env_file(folder: Path) -> None:
    (folder / ".env").write_text(f"GROQ_API_KEY={TEST_GROQ_KEY}\n")
    (folder / "README.md").write_text("not part of the package\n")


async def test_B10_S01_S04_package_holds_only_its_parts_and_no_secrets(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path, folder_setup=_with_env_file)
    response = await download(client, user, run_id)  # the response guard also searches the bytes for secrets
    files = unzip(response.content)
    assert all(name in ("summary.md", "changes.patch") or name.startswith(("fixed_files/", "logs/")) for name in files)
    assert not [name for name in files if name.endswith((".env", "pom.xml", "README.md", "DiscountTest.java"))]
    text = b"\n".join(files.values()).decode("utf-8", errors="replace")
    for secret in KNOWN_SECRETS:
        assert secret not in text
    for name in ("GROQ_API_KEY", "JWT_SECRET", "DATABASE_URL", "PATH="):
        assert name not in text


def test_B10_S02_no_zip_slip_names(tmp_path):
    assert safe_name("fixed_files", "../../etc/passwd") is None
    assert safe_name("fixed_files", "/etc/passwd") is None
    assert safe_name("fixed_files", "a\\..\\b") is None
    assert safe_name("fixed_files", "a\x00b") is None
    assert safe_name("fixed_files", "src/A.java") == "fixed_files/src/A.java"
    code = tmp_path / "code"
    (code / "src").mkdir(parents=True)
    (code / "src" / "A.java").write_text("a")
    (tmp_path / "secret.txt").write_text("outside")
    bad_paths = ["../secret.txt", "/etc/passwd", "src\\..\\..\\secret.txt", "src/A.java"]
    attempt = {"id": 1, "attempt_number": 1, "reason_started": "first", "explanation": "x", "decision": "accepted",
               "status": "tests_passed", "decision_feedback": None, "reverted_at": None,
               "edits": [{"file_path": p, "start_line": 1, "end_line": 1, "old_code": "a", "new_code": "b"}
                         for p in bad_paths]}
    run = {"id": 1, "language": "java", "test_command": "mvn -q test", "source_type": "zip", "source_reference": None,
           "source_commit": None, "llm_model": "m", "total_tokens": 0, "bug_description": "bug"}
    target = tmp_path / "out.zip"
    build_package(PackageInput(run, "succeeded", None, "ok", [attempt], [], "", code, None), target)
    names = zipfile.ZipFile(target).namelist()
    assert sorted(names) == ["changes.patch", "fixed_files/src/A.java", "summary.md"]
    assert not [n for n in names if n.startswith("/") or ".." in n.split("/") or "\\" in n]


async def test_B10_S02_real_package_names(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    names = zipfile.ZipFile(io.BytesIO((await download(client, user, run_id)).content)).namelist()
    assert not [n for n in names if n.startswith("/") or ".." in n.split("/") or "\\" in n or ":" in n]


async def test_B10_S03_another_users_package_and_apply(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    bob = await user_with_token(db, is_email_verified=True)
    assert_error(await download(client, bob, run_id), 404, "NOT_FOUND")
    assert_error(await apply(client, bob, run_id), 404, "NOT_FOUND")
    missing_download = await download(client, bob, 99999999)
    assert (await download(client, bob, run_id)).json() == missing_download.json()  # same answer, no leak
    assert "return 0.05;" in (folder / FILE).read_text()
    assert (await run_row(db, run_id))["applied_to_source_at"] is None


# --- Concurrency ---------------------------------------------------------------------------------------

async def test_B10_C01_two_apply_clicks_at_once(app, client, db, tmp_path):
    user, project, folder, run_id = await finished_run(app, client, db, tmp_path)
    responses = await asyncio.gather(*[apply(client, user, run_id) for _ in range(2)])
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert [r for r in responses if r.status_code == 409][0].json()["error"]["code"] == "RUN_STATE_INVALID"
    assert (folder / FILE).read_text().count("return 0.10;") == 1


async def test_B10_C02_cancel_and_the_graph_finish_at_the_same_moment(app, client, db, tmp_path):
    for n in range(6):
        install_agent(app, [*investigation(), answer(fix())], ["failed"])
        user, project, folder = await user_and_project(db, tmp_path, f"race{n}")
        run_id = await start_run(client, user, project)
        await wait_for(db, run_id, "waiting_for_approval")
        await wait_idle(app, run_id)
        progress = app.state.run_progress
        calls = [progress.finish(run_id, "failed", "llm_unavailable"), progress.cancel(run_id)]
        results = await asyncio.gather(*(calls if n % 2 else reversed(calls)))
        assert sum(1 for r in results if r) == 1  # exactly one of them ended the run
        row = await run_row(db, run_id)
        assert row["status"] in ("failed", "stopped") and row["workspace_deleted_at"] is not None
        assert len(await package_rows(db, run_id)) == 1
        finals = [e for e in await events(db, run_id)
                  if e["event_type"] == "status_changed" and e["details"]["status"] in FINAL]
        assert len(finals) == 1 and finals[0]["details"]["status"] == row["status"]
        assert len(list(app.state.settings.packages_dir.glob(f"{user['id']}/*.zip"))) == 1


async def test_B10_C02_cancel_while_the_last_tests_finish(app, client, db, tmp_path):
    for n, delay in enumerate((0.15, 0.2, 0.25)):
        install_agent(app, [*investigation(), answer(fix())], ["failed", "passed"], test_seconds=0.2)
        user, project, folder = await user_and_project(db, tmp_path, f"late{n}")
        run_id = await start_run(client, user, project)
        await wait_for(db, run_id, "waiting_for_approval", timeout=20)
        await wait_idle(app, run_id)
        await decide(client, user, run_id, "accepted")
        await wait_for(db, run_id, ("testing", *FINAL))
        await asyncio.sleep(delay)
        await client.post(f"/api/v1/runs/{run_id}/cancel", headers=user["headers"])
        row = await wait_for(db, run_id, FINAL)
        await wait_idle(app, run_id)
        assert len(await package_rows(db, run_id)) == 1
        finals = [e for e in await events(db, run_id)
                  if e["event_type"] == "status_changed" and e["details"]["status"] in FINAL]
        assert [e["details"]["status"] for e in finals] == [row["status"]]


async def test_B10_C03_cleanup_deletes_a_package_while_it_downloads(base_env, db, tmp_path):
    user = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="succeeded")
    target = Path(base_env["PACKAGES_DIR"]) / str(user["id"]) / f"run_{run['id']}.zip"
    target.parent.mkdir(parents=True)
    data = os.urandom(20 * 1024 * 1024)
    target.write_bytes(data)
    await insert_package(db, run["id"], storage_path=str(target), size_bytes=len(data))
    with running_server(base_env, tmp_path) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET /api/v1/runs/{run['id']}/package HTTP/1.1\r\nHost: test\r\nAuthorization: Bearer "
                     f"{user['token']}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        first = await reader.readexactly(64 * 1024)
        await expire_package(db, run["id"])
        settings_cleanup = RunCleanup(Path(base_env["PACKAGES_DIR"]))
        settings_cleanup.delete_package_file(str(target))  # what the cleanup job does, in the middle of the download
        rest = await reader.read()
        writer.close()
        assert b" 200 " in head.split(b"\r\n")[0]
        body = first + rest
        assert len(body) == len(data) and body == data  # the open file is read to the end
        after = await asyncio.to_thread(_get, port, f"/api/v1/runs/{run['id']}/package", user["token"])
        assert after.status_code == 410
        assert (await asyncio.to_thread(_get, port, "/api/v1/health", None)).status_code == 200


def _get(port: int, path: str, token: str | None):
    import httpx
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.get(f"http://127.0.0.1:{port}{path}", headers=headers, timeout=10)


# --- Recovery ------------------------------------------------------------------------------------------

async def test_B10_R01_running_the_cleanup_twice(app, db, settings):
    scene = await _cleanup_scene(db, settings)
    first = await app.state.jobs.run_cleanup()
    cur = await db.execute("SELECT id, deleted_at FROM packages ORDER BY id")
    packages_after_first = await cur.fetchall()
    files_after_first = sorted(str(p) for p in settings.packages_dir.rglob("*"))
    copies_after_first = sorted(str(p) for p in settings.workspaces_dir.rglob("*"))
    second = await app.state.jobs.run_cleanup()
    assert first == {"packages": 1, "workspaces": 2, "orphan_packages": 1, "tokens": 2}
    assert second == {"packages": 0, "workspaces": 0, "orphan_packages": 0, "tokens": 0}
    cur = await db.execute("SELECT id, deleted_at FROM packages ORDER BY id")
    assert await cur.fetchall() == packages_after_first
    assert sorted(str(p) for p in settings.packages_dir.rglob("*")) == files_after_first
    assert sorted(str(p) for p in settings.workspaces_dir.rglob("*")) == copies_after_first
    assert scene["fresh_file"].exists()


@pytest.mark.slow
async def test_B10_R02_restart_after_the_package_before_the_final_status(settings, db, tmp_path):
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        install_agent(app, [*investigation(), answer(fix())], ["failed", "passed"])
        user, project, folder = await user_and_project(db, tmp_path)
        run_id = await start_run(client, user, project)
        await wait_for(db, run_id, "waiting_for_approval")
        await wait_idle(app, run_id)
        never = asyncio.Event()

        async def server_stops_here(*args, **kwargs):
            await never.wait()

        app.state.run_progress.mark_finished = server_stops_here
        await decide(client, user, run_id, "accepted")
        deadline = time.monotonic() + 10
        while not await package_rows(db, run_id) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
    row = await run_row(db, run_id)
    assert row["status"] == "testing" and row["workspace_deleted_at"] is None
    [package] = await package_rows(db, run_id)
    first_bytes = Path(package["storage_path"]).read_bytes()

    app = create_app(settings)
    async with LifespanManager(app):
        row = await run_row(db, run_id)
        assert (row["status"], row["stop_reason"]) == ("failed", "server_restarted")
        assert row["workspace_deleted_at"] is not None
        assert await package_rows(db, run_id) == [package]  # no second package
        assert Path(package["storage_path"]).read_bytes() == first_bytes
        assert len(list(settings.packages_dir.rglob("*.zip"))) == 1
        finals = [e for e in await events(db, run_id) if e["event_type"] == "status_changed"
                  and e["details"]["status"] in FINAL]
        assert len(finals) == 1


@pytest.mark.slow
async def test_B10_R02_restart_after_the_final_status_before_the_cleanup(settings, db, tmp_path):
    """The other cut: the final status was saved but the copy was not deleted (and no package exists)."""
    user = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="failed", stop_reason="tests_failed_3_times",
                           final_summary="The run ended.")
    copy = settings.workspaces_dir / str(user["id"]) / str(run["id"])
    (copy / "artifacts").mkdir(parents=True)
    (copy / "artifacts" / "baseline.log").write_text("Tests failed")
    await db.execute("UPDATE runs SET workspace_path = %s WHERE id = %s", (str(copy), run["id"]))
    for _ in range(2):  # a second restart changes nothing
        app = create_app(settings)
        async with LifespanManager(app):
            row = await run_row(db, run["id"])
            assert row["status"] == "failed" and row["workspace_deleted_at"] is not None
            assert not copy.exists()
            [package] = await package_rows(db, run["id"])
            files = unzip(Path(package["storage_path"]).read_bytes())
            assert sorted(files) == ["attempts.md", "logs/baseline.log", "summary.md"]


# --- Load ----------------------------------------------------------------------------------------------

@pytest.mark.slow
async def test_B10_L01_ten_thousand_expired_packages(app, client, db, settings):
    user = await user_with_token(db)
    project = await insert_project(db, user["id"])
    folder = settings.packages_dir / str(user["id"])
    folder.mkdir(parents=True)
    await db.execute(
        "INSERT INTO runs (user_id, project_id, bug_description, status, language, test_command, source_type, "
        "source_reference, llm_model) SELECT %s, %s, 'Discount shows 5%% not 10%%', 'succeeded', 'java', 'mvn test', "
        "'github', 'https://github.com/o/r', 'm' FROM generate_series(1, 10000)", (user["id"], project["id"]))
    await db.execute(
        "INSERT INTO packages (run_id, storage_path, size_bytes, created_at, expires_at) "
        "SELECT id, %s || '/run_' || id || '.zip', 3, now() - interval '8 days', now() - interval '1 day' "
        "FROM runs WHERE user_id = %s", (str(folder), user["id"]))
    cur = await db.execute("SELECT id FROM runs WHERE user_id = %s", (user["id"],))
    ids = [r["id"] for r in await cur.fetchall()]
    await asyncio.to_thread(lambda: [(folder / f"run_{i}.zip").write_bytes(b"zip") for i in ids])

    lags: list[float] = []

    async def probe():
        while True:
            started = time.perf_counter()
            assert (await client.get("/api/v1/health")).status_code == 200
            lags.append((time.perf_counter() - started) * 1000)
            await asyncio.sleep(0.05)

    prober = asyncio.create_task(probe())
    started = time.perf_counter()
    results = await app.state.jobs.run_cleanup()
    total = time.perf_counter() - started
    prober.cancel()
    await asyncio.gather(prober, return_exceptions=True)
    print(f"\nB10-L01: 10,000 expired packages cleaned in {total:.1f} s (batches of "
          f"{settings.cleanup_batch_size}); /health during it: {len(lags)} calls, max {max(lags):.0f} ms")
    assert results["packages"] == 10000
    cur = await db.execute("SELECT count(*) AS n FROM packages WHERE deleted_at IS NULL")
    assert (await cur.fetchone())["n"] == 0
    assert list(folder.iterdir()) == []
    assert max(lags) < 500


def _rss_kib(pid: int, field: str) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    raise AssertionError(field)


async def _raw_download(port: int, path: str, token: str) -> tuple[int, int]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: test\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n"
                 .encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    size = 0
    while chunk := await reader.read(256 * 1024):
        size += len(chunk)
    writer.close()
    return int(head.split(b" ", 2)[1]), size


async def _stored_package(db, base_env, size: int) -> tuple[dict, dict]:
    user = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status="succeeded")
    target = Path(base_env["PACKAGES_DIR"]) / str(user["id"]) / f"run_{run['id']}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as out:
        for _ in range(size // (1024 * 1024)):
            out.write(os.urandom(1024 * 1024))
    await insert_package(db, run["id"], storage_path=str(target), size_bytes=size)
    return user, run


@pytest.mark.slow
async def test_B10_L02_download_a_50_mb_package(base_env, db, tmp_path):
    size = 50 * 1024 * 1024
    user, run = await _stored_package(db, base_env, size)
    with running_server(base_env, tmp_path, with_process=True) as (port, proc):
        await _raw_download(port, "/api/v1/health", user["token"])
        peak_before = _rss_kib(proc.pid, "VmHWM")
        started = time.perf_counter()
        status, received = await _raw_download(port, f"/api/v1/runs/{run['id']}/package", user["token"])
        seconds = time.perf_counter() - started
        peak_after = _rss_kib(proc.pid, "VmHWM")
    growth = (peak_after - peak_before) / 1024
    print(f"\nB10-L02: 50 MB download in {seconds:.2f} s; server peak memory grew by {growth:.1f} MB")
    assert status == 200 and received == size
    assert growth < 20


@pytest.mark.slow
async def test_B10_L03_twenty_parallel_downloads(base_env, db, tmp_path):
    size = 2 * 1024 * 1024
    stored = [await _stored_package(db, base_env, size) for _ in range(20)]
    with running_server(base_env, tmp_path) as port:
        started = time.perf_counter()
        results = await asyncio.gather(*[_raw_download(port, f"/api/v1/runs/{run['id']}/package", user["token"])
                                         for user, run in stored])
        seconds = time.perf_counter() - started
    print(f"\nB10-L03: 20 parallel downloads of 2 MB in {seconds:.2f} s")
    assert results == [(200, size)] * 20


@pytest.mark.slow
async def test_B10_L04_fifty_runs_finishing_at_the_same_moment(app, client, db, tmp_path, settings):
    install_responder(app)
    pairs = [await user_and_project(db, tmp_path, f"many{n}") for n in range(50)]
    run_ids = [await start_run(client, u, p) for u, p, _ in pairs]
    for run_id in run_ids:
        await wait_for(db, run_id, "waiting_for_approval", timeout=60)
    for run_id in run_ids:
        await wait_idle(app, run_id)
    responses = await asyncio.gather(*[decide(client, u, r, "accepted") for (u, _, _), r in zip(pairs, run_ids)])
    assert [r.status_code for r in responses] == [200] * 50
    for run_id in run_ids:
        assert (await wait_for(db, run_id, FINAL, timeout=60))["status"] == "succeeded"
    for run_id in run_ids:
        await wait_idle(app, run_id)
    cur = await db.execute("SELECT run_id, storage_path, size_bytes FROM packages WHERE run_id = ANY(%s)", (run_ids,))
    packages = {r["run_id"]: r for r in await cur.fetchall()}
    assert sorted(packages) == sorted(run_ids)
    assert len({p["storage_path"] for p in packages.values()}) == 50
    on_disk = list(settings.packages_dir.rglob("*.zip"))
    assert len(on_disk) == 50
    assert sum(p.stat().st_size for p in on_disk) == sum(p["size_bytes"] for p in packages.values())
    for (user, _, _), run_id in zip(pairs, run_ids):
        package = packages[run_id]
        assert Path(package["storage_path"]).parent.name == str(user["id"])
        summary = unzip(Path(package["storage_path"]).read_bytes())["summary.md"].decode()
        assert summary.startswith(f"# SANGRA_AI run {run_id}\n")
    print(f"\nB10-L04: 50 packages, {sum(p['size_bytes'] for p in packages.values()) / 1024:.0f} KiB on disk in all")
    assert [len(await attempts(db, r)) for r in run_ids] == [1] * 50
