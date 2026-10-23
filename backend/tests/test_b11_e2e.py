"""Phase B11 (part 1): end-to-end journeys through the HTTP API, from register to download.

The plan marks B11-E01..E07 as manual with the real Groq. Groq cannot be reached from the build machine, so these
tests run the same journeys with everything real except the model: the real routes, services, database, LangGraph
agent and checkpointer, workspace manager, and (for E01, E02 and E07) the real Docker test runner. The model is a
FakeLLM with scripted answers. The manual runs with the real Groq stay open (see the README, "Open items").
"""

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager

from app.agent.nodes.steps import AgentServices
from app.agent.workflow import LangGraphWorkflow
from app.main import create_app
from tests.agent_helpers import FILE, FOUND, NOT_FOUND, FakeTestRunner, fix, snapshot_rows, wait_for, wait_idle
from tests.conftest import FakeMailer, make_client, sandbox_network_extras
from tests.fake_llm import FakeLLM, answer, tool_call
from tests.sample_projects import JAVA_COMMAND, PYTHON_COMMAND, java_project, python_project

FINAL = ("succeeded", "failed", "stopped")
BUG = "The summer sale shows 5% discount, it should be 10%."
PASSWORD = "Str0ng-pass-word"
pytestmark = pytest.mark.settings(LLM_RETRY_BASE_SECONDS="0")

PY_FILE = "discount.py"
PY_FOUND = {"outcome": "found", "summary": "summer_rate returns 0.05",
            "findings": [{"file_path": PY_FILE, "start_line": 1, "end_line": 2, "note": "the summer rate"}]}


def py_fix(rate: str = "0.10") -> dict:
    return {"explanation": f"The summer rate must be {rate}.",
            "edits": [{"file_path": PY_FILE, "start_line": 2, "end_line": 2, "old_code": "    return 0.05",
                       "new_code": f"    return {rate}", "reason": "the rate"}]}


def plug_agent(app, script: list, *, real_docker: bool = False, tests: list[str] | None = None,
               latency: float = 0.0, test_seconds: float = 0.0):
    """The real LangGraph agent with a scripted model, and either the real Docker runner or a FakeTestRunner."""
    llm = FakeLLM(script, latency=latency)
    settings = app.state.settings
    if real_docker:
        from app.runners.test_runner import TestRunner

        env, mounts = sandbox_network_extras()
        runner = TestRunner(settings, extra_env=env, extra_mounts=mounts, cache_prefix="sangra-test-cache")
    else:
        runner = FakeTestRunner(tests or [], seconds=test_seconds)
    services = AgentServices(app.state.run_progress, app.state.workspace_manager, runner, lambda: llm, settings)
    app.state.agent_runner.workflow = LangGraphWorkflow(services, app.state.agent_workflow.checkpointer)
    return llm, runner


class Journey:
    """One user, through the HTTP API only."""

    def __init__(self, client, mailer: FakeMailer, email: str):
        self.client, self.mailer, self.email = client, mailer, email
        self.headers: dict[str, str] = {}

    async def call(self, method: str, path: str, expect: int = 200, **kwargs):
        response = await self.client.request(method, f"/api/v1{path}", headers=self.headers, **kwargs)
        assert response.status_code == expect, f"{method} {path}: {response.status_code} {response.text}"
        return response

    async def sign_up(self) -> None:
        await self.call("POST", "/auth/register", 201, json={"name": "Ann Lee", "email": self.email,
                                                             "password": PASSWORD})
        [token] = self.mailer.tokens(self.email, "verify-email")
        await self.call("POST", "/auth/verify-email", json={"token": token})
        login = await self.call("POST", "/auth/login", json={"email": self.email, "password": PASSWORD})
        self.headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        me = (await self.call("GET", "/auth/me")).json()["data"]
        assert me["is_email_verified"] is True

    async def project(self, **body) -> int:
        return (await self.call("POST", "/projects", 201, json=body)).json()["data"]["id"]

    async def start(self, project_id: int, zip_bytes: bytes | None = None) -> int:
        if zip_bytes is None:
            response = await self.call("POST", f"/projects/{project_id}/runs", 201, json={"bug_description": BUG})
        else:
            response = await self.call("POST", f"/projects/{project_id}/runs", 201, data={"bug_description": BUG},
                                       files={"zip_file": ("shop.zip", zip_bytes, "application/zip")})
        return response.json()["data"]["id"]

    async def detail(self, run_id: int) -> dict:
        return (await self.call("GET", f"/runs/{run_id}")).json()["data"]

    async def wait(self, run_id: int, statuses, timeout: float = 20.0) -> dict:
        statuses = (statuses,) if isinstance(statuses, str) else tuple(statuses)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            detail = await self.detail(run_id)
            if detail["status"] in statuses:
                return detail
            assert loop.time() < deadline, f"run {run_id} is {detail['status']} ({detail['stop_reason']})"
            await asyncio.sleep(0.05)

    async def decide(self, run_id: int, decision: str, feedback: str | None = None) -> None:
        detail = await self.detail(run_id)
        body = {"attempt_id": detail["attempts"][-1]["id"], "decision": decision}
        if feedback:
            body["feedback"] = feedback
        await self.call("POST", f"/runs/{run_id}/approval", json=body)

    async def package(self, run_id: int) -> dict[str, bytes]:
        response = await self.call("GET", f"/runs/{run_id}/package")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    async def events(self, run_id: int) -> list[dict]:
        from tests.sse import AsgiStream, parse_sse

        async with AsgiStream(self.client._transport.app, f"/api/v1/runs/{run_id}/events", self.headers) as stream:
            text = await stream.read_until_closed(timeout=10)
        return parse_sse(text)[0]


@pytest.fixture
async def journey(app, client, db):
    mailer = FakeMailer()
    app.state.mailer = mailer
    user = Journey(client, mailer, "ann.lee@example.com")
    await user.sign_up()
    return user


def zipped(folder: Path, top: str = "shop-main") -> bytes:
    """The folder as a GitHub-style zip (everything inside one top folder)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, f"{top}/{path.relative_to(folder).as_posix()}")
    return buffer.getvalue()


async def nothing_left(app, db, settings, user_id_folder: Path, run_id: int) -> None:
    await wait_idle(app, run_id)
    assert not app.state.agent_runner.is_running(run_id)
    assert await snapshot_rows(db, run_id) == 0
    assert not (user_id_folder / str(run_id)).exists()


def user_folder(settings, journey_detail_user_id: int) -> Path:
    return settings.workspaces_dir / str(journey_detail_user_id)


async def user_id(journey: Journey) -> int:
    return (await journey.call("GET", "/auth/me")).json()["data"]["id"]


# --- E01 to E07 ------------------------------------------------------------------------------------------

@pytest.mark.slow
async def test_B11_E01_java_zip_project_full_journey(app, journey, db, tmp_path, monkeypatch, settings):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    plug_agent(app, [tool_call("search_files", query="discount"), answer(FOUND), answer(fix())], real_docker=True)
    source = java_project(tmp_path / "shop", rate="0.05")
    project_id = await journey.project(name="Shop", language="java", source_type="zip", test_command=JAVA_COMMAND)
    run_id = await journey.start(project_id, zipped(source))

    waiting = await journey.wait(run_id, "waiting_for_approval", timeout=240)
    [edit] = waiting["attempts"][0]["edits"]
    assert (edit["file_path"], edit["new_code"]) == (FILE, "        return 0.10;")
    await wait_idle(app, run_id)
    await journey.decide(run_id, "accepted")
    done = await journey.wait(run_id, FINAL, timeout=240)
    assert (done["status"], done["stop_reason"]) == ("succeeded", None), done
    assert [(r["test_type"], r["status"]) for r in done["test_results"]] == [("baseline", "failed"),
                                                                             ("after_fix", "passed")]
    files = await journey.package(run_id)
    assert "+        return 0.10;" in files["changes.patch"].decode()
    assert b"return 0.10;" in files[f"fixed_files/{FILE}"]
    stream = await journey.events(run_id)
    assert stream[-1]["event"] == "status_changed" and stream[-1]["data"]["details"]["status"] == "succeeded"
    assert [e["id"] for e in stream] == list(range(1, len(stream) + 1))
    await nothing_left(app, db, settings, user_folder(settings, await user_id(journey)), run_id)
    print(f"\nB11-E01: Java zip journey succeeded, {len(stream)} events, package {sorted(files)}")


@pytest.mark.slow
async def test_B11_E02_python_project_full_journey(app, journey, db, tmp_path, monkeypatch, settings):
    """The plan asks for a public GitHub repo; there is no public repo with this seeded bug, so the Python sample
    is used from a local folder (cloning public GitHub repos is proven in B6). The GitHub variant stays manual."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    plug_agent(app, [tool_call("search_files", query="summer"), answer(PY_FOUND), answer(py_fix())], real_docker=True)
    folder = python_project(tmp_path / "pyshop", rate="0.05")
    project_id = await journey.project(name="Py shop", language="python", source_type="local_path",
                                       source_reference=str(folder), test_command=PYTHON_COMMAND)
    run_id = await journey.start(project_id)
    await journey.wait(run_id, "waiting_for_approval", timeout=240)
    await wait_idle(app, run_id)
    await journey.decide(run_id, "accepted")
    done = await journey.wait(run_id, FINAL, timeout=240)
    assert done["status"] == "succeeded", done
    assert "return 0.05" in (folder / PY_FILE).read_text()  # the original is untouched until "Apply"
    applied = await journey.call("POST", f"/runs/{run_id}/apply-to-source")
    assert applied.json()["data"]["applied_to_source_at"] is not None
    assert (folder / PY_FILE).read_text() == "def summer_rate():\n    return 0.10\n"
    await nothing_left(app, db, settings, user_folder(settings, await user_id(journey)), run_id)


async def test_B11_E03_reject_then_accept(app, journey, db, tmp_path, settings):
    llm, _ = plug_agent(app, [tool_call("search_files", query="discount"), answer(FOUND), answer(fix("0.15")),
                              answer(fix("0.10"))], tests=["failed", "passed"])
    folder = java_project(tmp_path / "shop", rate="0.05")
    project_id = await journey.project(name="Shop", language="java", source_type="local_path",
                                       source_reference=str(folder), test_command=JAVA_COMMAND)
    run_id = await journey.start(project_id)
    await journey.wait(run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    await journey.decide(run_id, "rejected", "It must be exactly 10 percent, not 15.")
    second = await journey.wait(run_id, "waiting_for_approval")
    while len(second["attempts"]) < 2:
        second = await journey.wait(run_id, "waiting_for_approval")
    assert "It must be exactly 10 percent" in str(llm.requests[-1])
    await wait_idle(app, run_id)
    await journey.decide(run_id, "accepted")
    done = await journey.wait(run_id, FINAL)
    assert (done["status"], done["reject_count"]) == ("succeeded", 1)
    assert [(a["decision"], a["status"]) for a in done["attempts"]] == [("rejected", "rejected"),
                                                                        ("accepted", "tests_passed")]
    await nothing_left(app, db, settings, user_folder(settings, await user_id(journey)), run_id)


async def test_B11_E04_nothing_found_then_a_hint(app, journey, db, tmp_path, settings):
    llm, _ = plug_agent(app, [tool_call("search_files", query="rebate"), answer(NOT_FOUND),
                              tool_call("search_files", query="summerRate"), answer(FOUND), answer(fix())],
                        tests=["failed", "passed"])
    folder = java_project(tmp_path / "shop", rate="0.05")
    project_id = await journey.project(name="Shop", language="java", source_type="local_path",
                                       source_reference=str(folder), test_command=JAVA_COMMAND)
    run_id = await journey.start(project_id)
    await journey.wait(run_id, "waiting_for_hint")
    await wait_idle(app, run_id)
    await journey.call("POST", f"/runs/{run_id}/hint", json={"text": "Look at summerRate in Discount.java"})
    await journey.wait(run_id, "waiting_for_approval")
    await wait_idle(app, run_id)
    assert "Look at summerRate" in str(llm.requests[2])
    await journey.decide(run_id, "accepted")
    done = await journey.wait(run_id, FINAL)
    assert (done["status"], done["hint_count"]) == ("succeeded", 1)
    await nothing_left(app, db, settings, user_folder(settings, await user_id(journey)), run_id)


@pytest.mark.parametrize("how", ["tests_fail", "rejects", "never_found"])
async def test_B11_E05_a_bug_the_agent_cannot_fix(app, journey, db, tmp_path, settings, how):
    search = [tool_call("search_files", query="discount"), answer(FOUND)]
    if how == "never_found":
        script, tests = [tool_call("search_files", query="x"), answer(NOT_FOUND)] * 4, ["failed"]
    else:
        script, tests = [*search, answer(fix("0.11")), answer(fix("0.12")), answer(fix("0.13"))], ["failed"] * 4
    llm, _ = plug_agent(app, script, tests=tests)
    folder = java_project(tmp_path / "shop", rate="0.05")
    project_id = await journey.project(name="Shop", language="java", source_type="local_path",
                                       source_reference=str(folder), test_command=JAVA_COMMAND)
    run_id = await journey.start(project_id)
    for n in range(4):
        detail = await journey.wait(run_id, ("waiting_for_approval", "waiting_for_hint", *FINAL))
        if detail["status"] in FINAL:
            break
        await wait_idle(app, run_id)
        if detail["status"] == "waiting_for_hint":
            await journey.call("POST", f"/runs/{run_id}/hint", json={"text": f"Try another place, number {n}"})
        else:
            await journey.decide(run_id, "accepted" if how == "tests_fail" else "rejected", "Not like this")
    done = await journey.wait(run_id, FINAL)
    expected = {"tests_fail": ("failed", "tests_failed_3_times"), "rejects": ("stopped", "reject_limit_reached"),
                "never_found": ("failed", "no_match_after_3_hints")}[how]
    assert (done["status"], done["stop_reason"]) == expected
    # The cost stays bounded: the limits cap the number of model calls (each fake call reports 100 tokens).
    print(f"\nB11-E05 ({how}): ended {expected}, {len(llm.requests)} model calls, {done['total_tokens']} tokens")
    assert len(llm.requests) <= 20 and done["total_tokens"] == 100 * len(llm.requests)
    await nothing_left(app, db, settings, user_folder(settings, await user_id(journey)), run_id)


@pytest.mark.parametrize("phase", ["searching", "waiting_for_hint", "waiting_for_approval", "baseline_testing",
                                   "testing_after_fix"])
async def test_B11_E06_cancel_during_each_phase(app, journey, db, tmp_path, settings, phase):
    found = phase != "waiting_for_hint"
    script = [tool_call("search_files", query="discount"), answer(FOUND if found else NOT_FOUND), answer(fix())]
    _, runner = plug_agent(app, script, tests=["failed", "passed"], latency=2.0 if phase == "searching" else 0.0,
                           test_seconds=3.0 if "testing" in phase else 0.0)
    folder = java_project(tmp_path / "shop", rate="0.05")
    project_id = await journey.project(name="Shop", language="java", source_type="local_path",
                                       source_reference=str(folder), test_command=JAVA_COMMAND)
    run_id = await journey.start(project_id)
    target = {"searching": "searching", "waiting_for_hint": "waiting_for_hint", "baseline_testing": "searching",
              "waiting_for_approval": "waiting_for_approval", "testing_after_fix": "waiting_for_approval"}[phase]
    await journey.wait(run_id, target)
    if phase == "baseline_testing":  # the baseline runs while the run is "searching", before the model is asked
        while not runner.calls:
            await asyncio.sleep(0.02)
    if phase == "testing_after_fix":
        await wait_idle(app, run_id)
        await journey.decide(run_id, "accepted")
        await journey.wait(run_id, "testing")
    await asyncio.sleep(0.3)
    cancelled = await journey.call("POST", f"/runs/{run_id}/cancel")
    assert (cancelled.json()["data"]["status"], cancelled.json()["data"]["stop_reason"]) == ("stopped",
                                                                                            "cancelled_by_user")
    await nothing_left(app, db, settings, user_folder(settings, await user_id(journey)), run_id)
    done = await journey.detail(run_id)
    assert done["status"] == "stopped" and done["package"]["available"] is True
    assert "return 0.05;" in (folder / FILE).read_text()
    finals = [e for e in await journey.events(run_id) if e["event"] == "status_changed"
              and e["data"]["details"]["status"] in FINAL]
    assert len(finals) == 1


@pytest.mark.slow
async def test_B11_E07_restart_while_waiting_for_approval(settings, db, tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    folder = java_project(tmp_path / "shop", rate="0.05")
    app = create_app(settings)
    async with LifespanManager(app), make_client(app) as client:
        mailer = FakeMailer()
        app.state.mailer = mailer
        user = Journey(client, mailer, "restart@example.com")
        await user.sign_up()
        plug_agent(app, [tool_call("search_files", query="discount"), answer(FOUND), answer(fix())],
                   real_docker=True)
        project_id = await user.project(name="Shop", language="java", source_type="local_path",
                                        source_reference=str(folder), test_command=JAVA_COMMAND)
        run_id = await user.start(project_id)
        await user.wait(run_id, "waiting_for_approval", timeout=240)
        await wait_idle(app, run_id)
        headers = user.headers

    app = create_app(settings)  # the server starts again
    async with LifespanManager(app), make_client(app) as client:
        plug_agent(app, [answer(fix())], real_docker=True)  # nothing more is asked of the model after approval
        user = Journey(client, FakeMailer(), "restart@example.com")
        user.headers = headers
        assert (await user.detail(run_id))["status"] == "waiting_for_approval"
        await user.decide(run_id, "accepted")
        done = await user.wait(run_id, FINAL, timeout=240)
        assert (done["status"], done["stop_reason"]) == ("succeeded", None), done
        assert [r["status"] for r in done["test_results"]] == ["failed", "passed"]
        await nothing_left(app, db, settings, user_folder(settings, await user_id(user)), run_id)
