"""Phase B5 (part 1): run routes, state rules, events, with a FAKE agent: functional, failure, security, concurrency."""

import asyncio
import io
import time

import pytest

from tests.fake_agent import EDIT, FakeWorkflow
from tests.factories import (
    insert_attempt,
    insert_edit,
    insert_package,
    insert_project,
    insert_run,
    insert_test_result,
    user_with_token,
)

ACTIVE = ["started", "searching", "waiting_for_hint", "waiting_for_approval", "applying", "testing"]
FINAL = ["succeeded", "failed", "stopped"]
BUG = "The summer sale shows 5% discount, it should be 10%."


def assert_error(response, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]["message"]


@pytest.fixture
async def alice(db):
    return await user_with_token(db, name="Alice", is_email_verified=True)


@pytest.fixture
async def bob(db):
    return await user_with_token(db, name="Bob", is_email_verified=True)


@pytest.fixture
async def project(db, alice):
    return await insert_project(db, alice["id"])


def install(app, **options) -> FakeWorkflow:
    fake = FakeWorkflow(app.state.run_progress, **options)
    app.state.agent_runner.workflow = fake
    return fake


async def start(client, user, project_id, bug: str = BUG):
    return await client.post(f"/api/v1/projects/{project_id}/runs", json={"bug_description": bug},
                             headers=user["headers"])


async def run_row(db, run_id: int) -> dict:
    cur = await db.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
    return await cur.fetchone()


async def wait_for_status(db, run_id: int, statuses, timeout: float = 5.0) -> dict:
    statuses = (statuses,) if isinstance(statuses, str) else tuple(statuses)
    deadline = time.monotonic() + timeout
    while True:
        row = await run_row(db, run_id)
        if row and row["status"] in statuses:
            return row
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} is {row and row['status']}, expected {statuses}")
        await asyncio.sleep(0.02)


async def events_of(db, run_id: int) -> list[dict]:
    cur = await db.execute("SELECT * FROM run_events WHERE run_id = %s ORDER BY sequence_number", (run_id,))
    return await cur.fetchall()


async def waiting_run(db, user, project, with_attempt: bool = True) -> tuple[dict, dict | None]:
    """A run paused at approval (inserted directly), with the attempt that waits."""
    run = await insert_run(db, user["id"], project["id"], status="waiting_for_approval")
    attempt = None
    if with_attempt:
        attempt = await insert_attempt(db, run["id"], status="waiting_for_approval")
        await insert_edit(db, attempt["id"])
    return run, attempt


# --- Functional ---------------------------------------------------------------------------------------

async def test_B5_F01_start_answers_at_once_while_the_agent_works(app, client, db, alice, project):
    install(app, delay=0.3)
    started = time.perf_counter()
    response = await start(client, alice, project["id"])
    elapsed = time.perf_counter() - started
    assert response.status_code == 201 and elapsed < 0.5
    run = response.json()["data"]
    assert run["status"] == "started" and run["bug_description"] == BUG
    assert run["test_command"] == project["test_command"] and run["llm_model"] == "openai/gpt-oss-120b"
    assert "user_id" not in run and "workspace_path" not in run
    await wait_for_status(db, run["id"], "waiting_for_approval")


async def test_B5_F02_list_filters_and_pages(client, db, alice, project):
    other_project = await insert_project(db, alice["id"], name="Other")
    ids = []
    for n in range(25):
        status = "failed" if n % 2 else "succeeded"
        target = other_project if n % 5 == 0 else project
        ids.append((await insert_run(db, alice["id"], target["id"], status=status))["id"])
    active = await insert_run(db, alice["id"], project["id"], status="waiting_for_hint")

    page1 = (await client.get("/api/v1/runs", headers=alice["headers"])).json()["data"]
    assert page1["total"] == 26 and len(page1["items"]) == 20 and page1["page"] == 1 and page1["limit"] == 20
    assert page1["items"][0]["id"] == active["id"]  # newest first
    assert page1["items"][0]["project_name"] == "Shop"
    page2 = (await client.get("/api/v1/runs?page=2", headers=alice["headers"])).json()["data"]
    assert len(page2["items"]) == 6
    assert not {r["id"] for r in page1["items"]} & {r["id"] for r in page2["items"]}

    by_project = (await client.get(f"/api/v1/runs?project_id={other_project['id']}&limit=50",
                                   headers=alice["headers"])).json()["data"]
    assert by_project["total"] == 5 and all(r["project_id"] == other_project["id"] for r in by_project["items"])
    failed = (await client.get("/api/v1/runs?status=failed&limit=50", headers=alice["headers"])).json()["data"]
    assert failed["total"] == 12 and all(r["status"] == "failed" for r in failed["items"])
    only_active = (await client.get("/api/v1/runs?status=active", headers=alice["headers"])).json()["data"]
    assert [r["id"] for r in only_active["items"]] == [active["id"]]


async def test_B5_F03_get_run_with_attempts_edits_results_package(client, db, alice, project):
    run = await insert_run(db, alice["id"], project["id"], status="succeeded")
    attempt = await insert_attempt(db, run["id"], status="tests_passed", decision="accepted")
    await insert_edit(db, attempt["id"])
    await insert_test_result(db, run["id"])
    await insert_test_result(db, run["id"], test_type="after_fix", attempt_id=attempt["id"], status="passed")
    await insert_package(db, run["id"], storage_path="/data/packages/x.zip")
    data = (await client.get(f"/api/v1/runs/{run['id']}", headers=alice["headers"])).json()["data"]
    assert data["id"] == run["id"] and data["status"] == "succeeded"
    assert len(data["attempts"]) == 1 and data["attempts"][0]["edits"][0]["old_code"] == "rate = 0.05;"
    assert {r["test_type"] for r in data["test_results"]} == {"baseline", "after_fix"}
    assert data["package"]["available"] is True and data["package"]["size_bytes"] == 1024
    assert "storage_path" not in data["package"] and "/data/packages" not in str(data)


async def test_B5_F04_scripted_run_emits_30_events_numbered_1_to_30(app, client, db, alice, project):
    install(app, tool_events=26)  # 26 + started + searching + waiting + approval_requested = 30
    run = (await start(client, alice, project["id"])).json()["data"]
    await wait_for_status(db, run["id"], "waiting_for_approval")
    await asyncio.sleep(0.05)
    events = await events_of(db, run["id"])
    assert [e["sequence_number"] for e in events] == list(range(1, 31))


async def test_B5_F05_approval_accepted(app, client, db, alice, project):
    fake = install(app, test_seconds=0.3)
    run = (await start(client, alice, project["id"])).json()["data"]
    await wait_for_status(db, run["id"], "waiting_for_approval")
    attempt_id = (await client.get(f"/api/v1/runs/{run['id']}", headers=alice["headers"])).json()["data"]["attempts"][0]["id"]
    response = await client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                                 json={"attempt_id": attempt_id, "decision": "accepted"})
    assert response.status_code == 200 and response.json()["data"]["status"] == "applying"
    await wait_for_status(db, run["id"], "succeeded")
    assert len(fake.resumes) == 1


async def test_B5_F06_approval_rejected_with_feedback(app, client, db, alice, project):
    fake = install(app)
    run = (await start(client, alice, project["id"])).json()["data"]
    await wait_for_status(db, run["id"], "waiting_for_approval")
    attempt_id = (await client.get(f"/api/v1/runs/{run['id']}", headers=alice["headers"])).json()["data"]["attempts"][0]["id"]
    response = await client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                                 json={"attempt_id": attempt_id, "decision": "rejected", "feedback": "Wrong file."})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reject_count"] == 1 and data["status"] == "searching"
    cur = await db.execute("SELECT decision, decision_feedback, status FROM attempts WHERE id = %s", (attempt_id,))
    assert await cur.fetchone() == {"decision": "rejected", "decision_feedback": "Wrong file.", "status": "rejected"}
    await wait_for_status(db, run["id"], "waiting_for_approval")  # the agent proposed again
    assert len(fake.resumes) == 1


async def test_B5_F07_hint(app, client, db, alice, project):
    fake = install(app, need_hint=True)
    run = (await start(client, alice, project["id"])).json()["data"]
    row = await wait_for_status(db, run["id"], "waiting_for_hint")
    assert row["hint_count"] == 1
    response = await client.post(f"/api/v1/runs/{run['id']}/hint", headers=alice["headers"],
                                 json={"text": "Look at Discount.java"})
    assert response.status_code == 200 and response.json()["data"]["status"] == "searching"
    hint_events = [e for e in await events_of(db, run["id"]) if e["event_type"] == "hint_received"]
    assert len(hint_events) == 1 and hint_events[0]["details"] == {"hint": "Look at Discount.java"}
    await wait_for_status(db, run["id"], "waiting_for_approval")
    assert fake.resumes == [(run["id"], {"hint": "Look at Discount.java"})]


async def test_B5_F08_cancel_an_active_run(app, client, db, alice, project):
    fake = install(app, pause_forever=True)
    finished = []

    async def hook(run):
        finished.append(run["id"])

    app.state.run_progress.finish_hooks.append(hook)
    run = (await start(client, alice, project["id"])).json()["data"]
    await wait_for_status(db, run["id"], "searching")
    response = await client.post(f"/api/v1/runs/{run['id']}/cancel", headers=alice["headers"])
    assert response.status_code == 200
    row = await run_row(db, run["id"])
    assert (row["status"], row["stop_reason"]) == ("stopped", "cancelled_by_user") and row["finished_at"]
    assert fake.cancelled == [run["id"]] and not app.state.agent_runner.is_running(run["id"])
    last = (await events_of(db, run["id"]))[-1]
    assert last["event_type"] == "status_changed" and last["details"] == {"status": "stopped",
                                                                          "stop_reason": "cancelled_by_user"}
    assert finished == [run["id"]]


async def test_B5_F09_delete_a_finished_run(app, client, db, alice, project, settings):
    run = await insert_run(db, alice["id"], project["id"], status="succeeded")
    attempt = await insert_attempt(db, run["id"], status="tests_passed")
    await insert_edit(db, attempt["id"])
    package_file = settings.packages_dir / f"{run['id']}.zip"
    package_file.write_bytes(b"zip")
    await insert_package(db, run["id"], storage_path=str(package_file))
    deleted_threads = []

    async def deleter(run_id):
        deleted_threads.append(run_id)

    app.state.run_cleanup.thread_deleters.append(deleter)
    response = await client.delete(f"/api/v1/runs/{run['id']}", headers=alice["headers"])
    assert response.status_code == 200 and response.json()["data"] == {"id": run["id"], "deleted": True}
    for table in ["runs", "attempts", "packages"]:
        cur = await db.execute(f"SELECT count(*) AS n FROM {table}")
        assert (await cur.fetchone())["n"] == 0, table
    assert not package_file.exists() and deleted_threads == [run["id"]]


async def test_B5_F10_every_status_change_has_its_event_and_the_last_is_final(app, client, db, alice, project):
    fake = install(app, need_hint=True)
    for n in range(5):
        run = (await start(client, alice, project["id"])).json()["data"]
        row = await wait_for_status(db, run["id"], ("waiting_for_hint", "waiting_for_approval"))
        if row["status"] == "waiting_for_hint":
            await client.post(f"/api/v1/runs/{run['id']}/hint", headers=alice["headers"], json={"text": "Discount"})
            await wait_for_status(db, run["id"], "waiting_for_approval")
        detail = (await client.get(f"/api/v1/runs/{run['id']}", headers=alice["headers"])).json()["data"]
        attempt_id = detail["attempts"][-1]["id"]
        if n % 2:
            await client.post(f"/api/v1/runs/{run['id']}/cancel", headers=alice["headers"])
        else:
            await client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                              json={"attempt_id": attempt_id, "decision": "accepted"})
        final = await wait_for_status(db, run["id"], FINAL)
        events = await events_of(db, run["id"])
        statuses = [e["details"]["status"] for e in events if e["event_type"] == "status_changed"]
        expected = (["started", "searching", "waiting_for_hint", "searching", "waiting_for_approval"]
                    + (["stopped"] if n % 2 else ["applying", "testing", "succeeded"]))
        assert statuses == expected
        assert events[-1]["event_type"] == "status_changed" and events[-1]["details"]["status"] == final["status"]
        assert [e["sequence_number"] for e in events] == list(range(1, len(events) + 1))
    assert len(fake.starts) == 5


# --- Failure --------------------------------------------------------------------------------------

@pytest.mark.parametrize("bug", ["x" * 9, "x" * 2001, "", "            "])
async def test_B5_X01_bad_bug_descriptions(app, client, alice, project, bug):
    install(app)
    message = assert_error(await start(client, alice, project["id"], bug), 422, "VALIDATION_ERROR")
    assert "bug_description" in message


async def test_B5_X02_zip_rules(app, client, db, alice, settings):
    install(app, passive=True)
    zip_project = await insert_project(db, alice["id"], source_type="zip", source_reference=None)
    url = f"/api/v1/projects/{zip_project['id']}/runs"
    assert_error(await client.post(url, json={"bug_description": BUG}, headers=alice["headers"]), 400, "ZIP_REQUIRED")
    not_zip = await client.post(url, headers=alice["headers"], data={"bug_description": BUG},
                                files={"zip_file": ("code.txt", b"hello", "text/plain")})
    assert "zip_file" in assert_error(not_zip, 422, "VALIDATION_ERROR")
    just_over = await client.post(url, headers=alice["headers"], data={"bug_description": BUG},
                                  files={"zip_file": ("code.zip", b"x" * (50 * 1024 * 1024 + 1), "application/zip")})
    assert "zip_file" in assert_error(just_over, 422, "VALIDATION_ERROR")
    far_over = await client.post(url, headers=alice["headers"], data={"bug_description": BUG},
                                 files={"zip_file": ("code.zip", b"x" * (60 * 1024 * 1024), "application/zip")})
    assert_error(far_over, 413, "PAYLOAD_TOO_LARGE")

    # A 1 GB upload without a length is cut off while it streams in, long before it is all read.
    pulled = 0

    async def huge():
        nonlocal pulled
        boundary_start = b'--b\r\nContent-Disposition: form-data; name="zip_file"; filename="big.zip"\r\n\r\n'
        yield boundary_start
        for _ in range(1024):
            pulled += 1024 * 1024
            yield b"x" * (1024 * 1024)

    streamed = await client.post(url, headers={**alice["headers"], "content-type": "multipart/form-data; boundary=b"},
                                 content=huge())
    assert_error(streamed, 413, "PAYLOAD_TOO_LARGE")
    assert pulled < 60 * 1024 * 1024
    cur = await db.execute("SELECT count(*) AS n FROM runs")
    assert (await cur.fetchone())["n"] == 0

    ok = await client.post(url, headers=alice["headers"], data={"bug_description": BUG},
                           files={"zip_file": ("code.zip", b"PK\x03\x04 small zip", "application/zip")})
    assert ok.status_code == 201
    saved = settings.workspaces_dir / str(alice["id"]) / str(ok.json()["data"]["id"]) / "source.zip"
    assert saved.read_bytes() == b"PK\x03\x04 small zip"


async def test_B5_X02_zip_sent_to_a_non_zip_project_is_ignored(app, client, alice, project, settings):
    install(app, passive=True)
    response = await client.post(f"/api/v1/projects/{project['id']}/runs", headers=alice["headers"],
                                 data={"bug_description": BUG},
                                 files={"zip_file": ("code.zip", b"PK zip", "application/zip")})
    assert response.status_code == 201
    assert not (settings.workspaces_dir / str(alice["id"]) / str(response.json()["data"]["id"])).exists()


async def test_B5_X03_unverified_user_cannot_start(app, client, db):
    install(app, passive=True)
    user = await user_with_token(db, is_email_verified=False)
    project = await insert_project(db, user["id"])
    assert_error(await start(client, user, project["id"]), 403, "EMAIL_NOT_VERIFIED")


async def test_B5_X04_someone_elses_or_a_missing_project(app, client, alice, bob, project):
    install(app, passive=True)
    assert_error(await start(client, bob, project["id"]), 404, "NOT_FOUND")
    assert_error(await start(client, alice, 999_999), 404, "NOT_FOUND")


async def test_B5_X05_X06_one_active_run_then_a_new_one(app, client, db, alice, project):
    install(app, passive=True)
    first = (await start(client, alice, project["id"])).json()["data"]
    assert_error(await start(client, alice, project["id"]), 409, "ACTIVE_RUN_EXISTS")
    await client.post(f"/api/v1/runs/{first['id']}/cancel", headers=alice["headers"])
    assert (await start(client, alice, project["id"])).status_code == 201


async def test_B5_X07_approval_errors(app, client, db, alice, project):
    install(app, passive=True)
    searching = await insert_run(db, alice["id"], project["id"], status="searching")
    old = await insert_attempt(db, searching["id"], status="rejected", decision="rejected")
    url = f"/api/v1/runs/{searching['id']}/approval"
    assert_error(await client.post(url, headers=alice["headers"], json={"attempt_id": old["id"], "decision": "accepted"}),
                 409, "RUN_STATE_INVALID")
    await db.execute("UPDATE runs SET status = 'waiting_for_approval' WHERE id = %s", (searching["id"],))
    current = await insert_attempt(db, searching["id"], attempt_number=2, status="waiting_for_approval")
    assert_error(await client.post(url, headers=alice["headers"], json={"attempt_id": old["id"], "decision": "accepted"}),
                 409, "STALE_ATTEMPT")
    assert_error(await client.post(url, headers=alice["headers"], json={"attempt_id": current["id"], "decision": "maybe"}),
                 422, "VALIDATION_ERROR")
    for feedback in [None, "", "no", "x" * 2001]:
        body = {"attempt_id": current["id"], "decision": "rejected", "feedback": feedback}
        assert_error(await client.post(url, headers=alice["headers"], json=body), 422, "VALIDATION_ERROR")
    for bad_id in [0, -1, "abc"]:
        assert_error(await client.post(url, headers=alice["headers"], json={"attempt_id": bad_id, "decision": "accepted"}),
                     422, "VALIDATION_ERROR")


async def test_B5_X08_hint_errors(app, client, db, alice, project):
    install(app, passive=True)
    run = await insert_run(db, alice["id"], project["id"], status="searching")
    url = f"/api/v1/runs/{run['id']}/hint"
    assert_error(await client.post(url, headers=alice["headers"], json={"text": "Discount.java"}), 409, "RUN_STATE_INVALID")
    await db.execute("UPDATE runs SET status = 'waiting_for_hint' WHERE id = %s", (run["id"],))
    assert_error(await client.post(url, headers=alice["headers"], json={"text": "ab"}), 422, "VALIDATION_ERROR")
    assert_error(await client.post(url, headers=alice["headers"], json={"text": "x" * 2001}), 422, "VALIDATION_ERROR")


async def test_B5_X09_cancel_finished_and_delete_active(app, client, db, alice, project):
    install(app, passive=True)
    finished = await insert_run(db, alice["id"], project["id"], status="succeeded")
    active = await insert_run(db, alice["id"], project["id"], status="testing")
    assert_error(await client.post(f"/api/v1/runs/{finished['id']}/cancel", headers=alice["headers"]), 409,
                 "RUN_STATE_INVALID")
    assert_error(await client.delete(f"/api/v1/runs/{active['id']}", headers=alice["headers"]), 409, "RUN_STATE_INVALID")


ALLOWED = {
    "approval": {"waiting_for_approval"},
    "hint": {"waiting_for_hint"},
    "cancel": set(ACTIVE),
    "delete": set(FINAL),
}


@pytest.mark.parametrize("action", ["approval", "hint", "cancel", "delete"])
@pytest.mark.parametrize("status", ACTIVE + FINAL)
async def test_B5_X10_status_x_action_table(app, client, db, status, action):
    install(app, passive=True)
    user = await user_with_token(db, is_email_verified=True)
    project = await insert_project(db, user["id"])
    run = await insert_run(db, user["id"], project["id"], status=status)
    attempt = await insert_attempt(db, run["id"], status="waiting_for_approval")
    base = f"/api/v1/runs/{run['id']}"
    request = {
        "approval": lambda: client.post(f"{base}/approval", headers=user["headers"],
                                        json={"attempt_id": attempt["id"], "decision": "accepted"}),
        "hint": lambda: client.post(f"{base}/hint", headers=user["headers"], json={"text": "look here"}),
        "cancel": lambda: client.post(f"{base}/cancel", headers=user["headers"]),
        "delete": lambda: client.delete(base, headers=user["headers"]),
    }[action]
    response = await request()
    if status in ALLOWED[action]:
        assert response.status_code == 200, response.text
    else:
        assert_error(response, 409, "RUN_STATE_INVALID")
        row = await run_row(db, run["id"])
        assert row["status"] == status  # a refused action changes nothing (a finished run never changes again)


async def test_B5_X11_counters_are_exact(app, client, db, alice, project):
    install(app, passive=True)
    run, attempt = await waiting_run(db, alice, project)
    await client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                      json={"attempt_id": attempt["id"], "decision": "rejected", "feedback": "Not this one"})
    assert (await run_row(db, run["id"]))["reject_count"] == 1
    outcome = await app.state.run_progress.request_hint(run["id"])  # the run is searching again
    assert outcome["outcome"] == "ok" and (await run_row(db, run["id"]))["hint_count"] == 1


@pytest.mark.parametrize("query", ["status=foo", "limit=51", "limit=0", "page=0", "page=-1", "project_id=abc",
                                   "project_id=0", "page=abc"])
async def test_B5_X12_bad_list_queries(client, alice, query):
    assert_error(await client.get(f"/api/v1/runs?{query}", headers=alice["headers"]), 422, "VALIDATION_ERROR")


async def test_B5_X14_the_agent_crashes(app, client, db, alice, project, log_output):
    install(app, crash=True)
    run = (await start(client, alice, project["id"])).json()["data"]
    row = await wait_for_status(db, run["id"], FINAL)
    assert (row["status"], row["stop_reason"]) == ("failed", "internal_error")
    assert "the fake agent crashed" in log_output.getvalue()
    install(app, passive=True)
    assert (await start(client, alice, project["id"])).status_code == 201


# --- Security -------------------------------------------------------------------------------------

async def test_B5_S01_another_user_gets_404_on_every_run_route(app, client, db, alice, bob, project):
    install(app, passive=True)
    run, attempt = await waiting_run(db, alice, project)
    finished = await insert_run(db, alice["id"], project["id"], status="succeeded")
    base, done = f"/api/v1/runs/{run['id']}", f"/api/v1/runs/{finished['id']}"
    calls = [
        client.get(base, headers=bob["headers"]),
        client.get(f"{base}/events", headers=bob["headers"]),
        client.post(f"{base}/approval", headers=bob["headers"], json={"attempt_id": attempt["id"], "decision": "accepted"}),
        client.post(f"{base}/hint", headers=bob["headers"], json={"text": "hint text"}),
        client.post(f"{base}/cancel", headers=bob["headers"]),
        client.delete(done, headers=bob["headers"]),
        client.get(f"{done}/package", headers=bob["headers"]),
        client.post(f"{done}/apply-to-source", headers=bob["headers"]),
    ]
    for response in [await call for call in calls]:
        assert_error(response, 404, "NOT_FOUND")
    row = await run_row(db, run["id"])
    assert row["status"] == "waiting_for_approval" and row["reject_count"] == 0
    assert (await run_row(db, finished["id"])) is not None


async def test_B5_S02_stream_without_a_jwt(client, db, alice, project):
    run = await insert_run(db, alice["id"], project["id"], status="succeeded")
    assert_error(await client.get(f"/api/v1/runs/{run['id']}/events"), 401, "UNAUTHORIZED")


async def test_B5_S03_events_hold_no_file_contents(app, client, db, alice, project):
    install(app)
    run = (await start(client, alice, project["id"])).json()["data"]
    await wait_for_status(db, run["id"], "waiting_for_approval")
    text = str(await events_of(db, run["id"]))
    assert "SECRET_OLD_CODE" not in text and "SECRET_NEW_CODE" not in text and EDIT["old_code"] not in text


# --- Concurrency ----------------------------------------------------------------------------------

async def test_B5_C01_20_parallel_accepts(app, client, db, alice, project):
    fake = install(app, passive=True)
    run, attempt = await waiting_run(db, alice, project)
    responses = await asyncio.gather(*(
        client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                    json={"attempt_id": attempt["id"], "decision": "accepted"}) for _ in range(20)))
    codes = [r.status_code for r in responses]
    assert codes.count(200) == 1 and codes.count(409) == 19
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 409} <= {"RUN_STATE_INVALID", "STALE_ATTEMPT"}
    await asyncio.sleep(0.05)
    assert len(fake.resumes) == 1


async def test_B5_C02_accept_and_cancel_at_the_same_moment(app, client, db, alice, project):
    install(app, passive=True)
    for _ in range(50):
        run, attempt = await waiting_run(db, alice, project)
        accept, cancel = await asyncio.gather(
            client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                        json={"attempt_id": attempt["id"], "decision": "accepted"}),
            client.post(f"/api/v1/runs/{run['id']}/cancel", headers=alice["headers"]),
        )
        assert accept.status_code in (200, 409) and cancel.status_code in (200, 409)
        row = await run_row(db, run["id"])
        cur = await db.execute("SELECT decision FROM attempts WHERE id = %s", (attempt["id"],))
        decision = (await cur.fetchone())["decision"]
        # Consistent: the cancel always ends the run (a cancel is also allowed while applying). The decision is
        # saved only when the Accept landed first; never "applying" and "stopped" mixed.
        assert row["status"] == "stopped" and row["stop_reason"] == "cancelled_by_user"
        assert (decision == "accepted") == (accept.status_code == 200)
        assert cancel.status_code == 200


async def test_B5_C03_accept_and_reject_from_two_tabs(app, client, db, alice, project):
    install(app, passive=True)
    for _ in range(20):
        run, attempt = await waiting_run(db, alice, project)
        accept, reject = await asyncio.gather(
            client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                        json={"attempt_id": attempt["id"], "decision": "accepted"}),
            client.post(f"/api/v1/runs/{run['id']}/approval", headers=alice["headers"],
                        json={"attempt_id": attempt["id"], "decision": "rejected", "feedback": "Other tab"}),
        )
        assert sorted([accept.status_code, reject.status_code]) == [200, 409]
        winner = "accepted" if accept.status_code == 200 else "rejected"
        cur = await db.execute("SELECT decision FROM attempts WHERE id = %s", (attempt["id"],))
        assert (await cur.fetchone())["decision"] == winner
        row = await run_row(db, run["id"])
        assert row["status"] == ("applying" if winner == "accepted" else "searching")
        await db.execute("UPDATE runs SET status = 'failed' WHERE id = %s", (run["id"],))


async def test_B5_C04_50_parallel_starts(app, client, db, alice, project):
    install(app, passive=True)
    responses = await asyncio.gather(*(start(client, alice, project["id"]) for _ in range(50)))
    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1 and codes.count(409) == 49
    cur = await db.execute("SELECT count(*) AS n FROM runs")
    assert (await cur.fetchone())["n"] == 1


async def test_B5_C05_five_writers_add_100_events_each(app, db, alice, project):
    run = await insert_run(db, alice["id"], project["id"], status="searching")
    progress = app.state.run_progress

    async def writer(name):
        for n in range(100):
            await progress.emit(run["id"], "tool_called", f"{name} {n}")

    await asyncio.gather(*(writer(f"w{k}") for k in range(5)))
    events = await events_of(db, run["id"])
    assert [e["sequence_number"] for e in events] == list(range(1, 501))


async def test_B5_C06_delete_and_cancel_at_the_same_moment(app, client, db, alice, project):
    install(app, passive=True)
    for _ in range(20):
        run = await insert_run(db, alice["id"], project["id"], status="searching")
        await insert_attempt(db, run["id"], status="proposed")
        deleted, cancelled = await asyncio.gather(
            client.delete(f"/api/v1/runs/{run['id']}", headers=alice["headers"]),
            client.post(f"/api/v1/runs/{run['id']}/cancel", headers=alice["headers"]),
        )
        assert deleted.status_code in (200, 409) and cancelled.status_code in (200, 404, 409)
        row = await run_row(db, run["id"])
        cur = await db.execute("SELECT count(*) AS n FROM attempts WHERE run_id = %s", (run["id"],))
        attempts = (await cur.fetchone())["n"]
        if row is None:  # deleted after the cancel ended it: nothing half deleted
            assert deleted.status_code == 200 and attempts == 0
        else:
            assert row["status"] == "stopped" and attempts == 1 and deleted.status_code == 409


async def test_B5_C07_start_a_run_and_delete_the_project_at_the_same_moment(app, client, db, alice):
    install(app, passive=True)
    for _ in range(20):
        project = await insert_project(db, alice["id"])
        started, deleted = await asyncio.gather(
            start(client, alice, project["id"]),
            client.delete(f"/api/v1/projects/{project['id']}", headers=alice["headers"]),
        )
        if started.status_code == 201:
            assert deleted.status_code == 409 and deleted.json()["error"]["code"] == "PROJECT_HAS_ACTIVE_RUN"
            await db.execute("UPDATE runs SET status = 'failed' WHERE project_id = %s", (project["id"],))
        else:
            assert started.status_code == 404 and deleted.status_code == 200
        assert started.status_code != 500 and deleted.status_code != 500
