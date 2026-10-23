"""RunService (Layer 4): start, list, get, stream, approval, hint, cancel, delete.

Background work: an agent run can take minutes, so start_run answers at once and the agent keeps working in
the background (AgentRunner). The screen follows along through the live stream.
"""

import asyncio
import errno
import logging
import os
import shutil
import stat
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from psycopg_pool import AsyncConnectionPool

from app.agent.edits import Edit, EditError, plan_edits, write_planned_all_or_nothing
from app.agent.runner import AgentRunner
from app.core.config import Settings
from app.core.result import Result
from app.repositories import (
    attempt_repository,
    event_repository,
    package_repository,
    project_repository,
    run_repository,
    test_result_repository,
)
from app.repositories.event_repository import FINAL_STATUSES
from app.repositories.run_repository import ACTIVE_STATUSES
from app.services.cleanup_service import RunCleanup
from app.services.run_progress import RunProgress

logger = logging.getLogger("app.run")

NOT_FOUND_MESSAGE = "Run not found."
STREAM_BATCH = 500

# Fields the browser may see (no user_id, no server paths).
PUBLIC_RUN_FIELDS = (
    "id", "project_id", "bug_description", "status", "language", "test_command", "branch", "source_type",
    "source_reference", "source_commit", "llm_model", "stop_reason", "reject_count", "failed_test_count",
    "hint_count", "final_summary", "total_tokens", "applied_to_source_at", "started_at", "finished_at",
    "created_at", "updated_at",
)


@dataclass(frozen=True)
class RunContext:
    pool: AsyncConnectionPool
    settings: Settings
    runner: AgentRunner
    progress: RunProgress
    cleanup: RunCleanup


def public_run(run: dict) -> dict:
    return {field: run.get(field) for field in PUBLIC_RUN_FIELDS}


def public_package(package: dict | None) -> dict | None:
    if package is None:
        return None
    return {
        "size_bytes": package["size_bytes"],
        "created_at": package["created_at"],
        "expires_at": package["expires_at"],
        "deleted_at": package["deleted_at"],
        "available": package["deleted_at"] is None and not package.get("expired", False),
    }


def upload_path(settings: Settings, user_id: int, run_id: int) -> Path:
    """Where the uploaded zip waits for the agent: workspaces/<user_id>/<run_id>/source.zip."""
    return settings.workspaces_dir / str(user_id) / str(run_id) / "source.zip"


def _save_upload(source: BinaryIO, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source.seek(0)
    with target.open("wb") as out:
        shutil.copyfileobj(source, out, length=1024 * 1024)


def _not_found() -> Result:
    return Result.failure("NOT_FOUND", NOT_FOUND_MESSAGE)


def _state_invalid(action: str) -> Result:
    return Result.failure("RUN_STATE_INVALID", f"You cannot {action} in the run's current state.")


async def start_run(ctx: RunContext, user: dict, project_id: int, bug_description: str,
                    zip_file: BinaryIO | None) -> Result:
    project = await project_repository.find_by_id_and_user(ctx.pool, project_id, user["id"])
    if project is None:
        return Result.failure("NOT_FOUND", "Project not found.")
    if not user["is_email_verified"]:
        return Result.failure("EMAIL_NOT_VERIFIED", "Please verify your email before starting a run.")
    if project["source_type"] == "local_path" and ctx.settings.app_mode != "local":
        return Result.failure("LOCAL_PATH_NOT_ALLOWED",
                              "Local folders can only be used when the backend runs on your own machine.")
    if project["source_type"] == "zip" and zip_file is None:
        return Result.failure("ZIP_REQUIRED", "This is a zip project: please upload the zip file.")

    created = await run_repository.create_run(ctx.pool, user["id"], project, bug_description,
                                              ctx.settings.groq_model)
    if created["outcome"] == "active_run_exists":
        return Result.failure("ACTIVE_RUN_EXISTS", "You already have a run in progress. Wait for it or cancel it.")
    if created["outcome"] == "project_gone":
        return Result.failure("NOT_FOUND", "Project not found.")
    run = created["run"]

    # A zip sent for a non-zip project is ignored: only zip projects take their code from an upload.
    if project["source_type"] == "zip" and zip_file is not None:
        try:
            await asyncio.to_thread(_save_upload, zip_file, upload_path(ctx.settings, user["id"], run["id"]))
        except Exception:
            await ctx.progress.finish(run["id"], "failed", "internal_error",
                                      "The uploaded zip could not be saved on the server.")
            raise

    ctx.runner.start(run["id"])
    return Result.success(public_run(run))


async def list_runs(ctx: RunContext, user: dict, project_id: int | None, status: str | None, page: int,
                    limit: int) -> Result:
    statuses = list(ACTIVE_STATUSES) if status == "active" else ([status] if status else None)
    rows, total = await run_repository.list_by_user(ctx.pool, user["id"], project_id, statuses, limit,
                                                    (page - 1) * limit)
    return Result.success({"items": rows, "total": total, "page": page, "limit": limit})


async def get_run(ctx: RunContext, user: dict, run_id: int) -> Result:
    run = await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"])
    if run is None:
        return _not_found()
    attempts = await attempt_repository.list_with_edits(ctx.pool, run_id)
    results = await test_result_repository.list_by_run(ctx.pool, run_id)
    package = await package_repository.find_by_run(ctx.pool, run_id)
    return Result.success({**public_run(run), "attempts": attempts, "test_results": results,
                           "package": public_package(package)})


async def open_event_stream(ctx: RunContext, user: dict, run_id: int, after_sequence: int) -> Result:
    """NOT_FOUND if not theirs; else the data is an async iterator of event batches. It sends every saved
    event after after_sequence (catch-up), then keeps checking about once a second and stops after the
    final status_changed event. Between checks it holds no database connection."""
    if await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"]) is None:
        return _not_found()
    return Result.success(_event_batches(ctx, run_id, after_sequence))


def is_final_event(event: dict) -> bool:
    return event["event_type"] == "status_changed" and (event["details"] or {}).get("status") in FINAL_STATUSES


async def _event_batches(ctx: RunContext, run_id: int, after_sequence: int) -> AsyncIterator[list[dict]]:
    last = after_sequence
    while True:
        status, events = await event_repository.poll_for_stream(ctx.pool, run_id, last, STREAM_BATCH)
        if status is None:  # the run was deleted meanwhile
            return
        if events:
            last = events[-1]["sequence_number"]
            final_index = next((i for i, e in enumerate(events) if is_final_event(e)), None)
            if final_index is not None:
                yield events[: final_index + 1]
                return
            yield events
            if len(events) == STREAM_BATCH:
                continue  # more to catch up at once
        elif status in FINAL_STATUSES:
            return  # finished, and the browser already has every event
        else:
            yield []  # nothing new: lets the controller send a ping when it is time
        await asyncio.sleep(ctx.settings.stream_poll_seconds)


async def submit_approval(ctx: RunContext, user: dict, run_id: int, attempt_id: int, decision: str,
                          feedback: str | None) -> Result:
    if await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"]) is None:
        return _not_found()
    outcome = await attempt_repository.record_approval_decision(
        ctx.pool, run_id, attempt_id, decision, feedback if decision == "rejected" else None)
    if outcome["outcome"] == "run_not_waiting":
        return _state_invalid("answer a proposal")
    if outcome["outcome"] == "stale_attempt":
        return Result.failure("STALE_ATTEMPT", "This proposal is not the one waiting for an answer any more.")
    # Either way the agent resumes; it reads the decision (and reject_count) from the database.
    ctx.runner.resume(run_id, {"decision": decision, "attempt_id": attempt_id})
    return Result.success(public_run(outcome["run"]))


async def submit_hint(ctx: RunContext, user: dict, run_id: int, text: str) -> Result:
    if await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"]) is None:
        return _not_found()
    run = await run_repository.record_hint(ctx.pool, run_id, text)
    if run is None:
        return _state_invalid("send a hint")
    ctx.runner.resume(run_id, {"hint": text})
    return Result.success(public_run(run))


async def cancel_run(ctx: RunContext, user: dict, run_id: int) -> Result:
    run = await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"])
    if run is None:
        return _not_found()
    if run["status"] not in ACTIVE_STATUSES:
        return _state_invalid("cancel the run")
    # Mark stopped (conditional), stop the agent and its container, undo, package, delete the copy.
    finished = await ctx.progress.cancel(run_id)
    if finished is None:
        return _state_invalid("cancel the run")
    return Result.success(public_run(finished))


async def delete_run(ctx: RunContext, user: dict, run_id: int) -> Result:
    outcome = await run_repository.delete_run(ctx.pool, run_id, user["id"])
    if outcome["outcome"] == "not_found":
        return _not_found()
    if outcome["outcome"] == "active":
        return _state_invalid("delete an active run (cancel it first)")
    # After the commit: the package file and the LangGraph data. Failures are logged inside.
    await ctx.cleanup.after_runs_deleted([run_id], outcome["package_paths"])
    return Result.success({"id": run_id, "deleted": True})


def _package_problem(package: dict | None) -> Result | None:
    if package is None:
        return _state_invalid("download the package yet (it is built when the run ends)")
    if package["deleted_at"] is not None or package["expired"]:
        return Result.failure("PACKAGE_EXPIRED", "The package has expired (packages are kept 7 days).")
    return None


async def get_package(ctx: RunContext, user: dict, run_id: int) -> Result:
    """NOT_FOUND if not theirs; not built yet -> RUN_STATE_INVALID; deleted or expired -> PACKAGE_EXPIRED."""
    if await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"]) is None:
        return _not_found()
    package = await package_repository.find_by_run(ctx.pool, run_id)
    if problem := _package_problem(package):
        return problem
    return Result.success({"path": package["storage_path"], "filename": f"sangra-run-{run_id}.zip"})


def _apply_to_folder(folder: Path, edits: list[dict], max_edits: int, max_changed_lines: int) -> None:
    planned = plan_edits(folder, [Edit.from_dict(e) for e in edits], max_edits, max_changed_lines)
    for path in planned:
        # A read-only file is refused (a rename over it would succeed on most systems and silently change it).
        if not path.stat().st_mode & stat.S_IWUSR or not os.access(path, os.W_OK):
            raise PermissionError(errno.EACCES, "the file is read-only", str(path))
    write_planned_all_or_nothing(planned)


async def apply_to_source(ctx: RunContext, user: dict, run_id: int) -> Result:
    """"Apply to my folder". The user may have edited the folder after the run started, so whole files are never
    overwritten: the same edits are applied again with the same old_code check on the exact lines. ALL edits are
    checked first; if any no longer matches, nothing is written (SOURCE_CHANGED)."""
    run = await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"])
    if run is None:
        return _not_found()
    if ctx.settings.app_mode != "local" or run["source_type"] != "local_path":
        return Result.failure("LOCAL_PATH_NOT_ALLOWED",
                              "Apply to my folder works only for local folder projects when the backend runs on your "
                              "own machine. Download the package instead.")
    if run["status"] != "succeeded" or run["applied_to_source_at"] is not None:
        return _state_invalid("apply this run to your folder (only a succeeded run, once)")
    if problem := _package_problem(await package_repository.find_by_run(ctx.pool, run_id)):
        return problem if problem.error_code == "PACKAGE_EXPIRED" else Result.failure(
            "PACKAGE_EXPIRED", "The package of this run is not available.")
    # Claim first (one conditional update): a second click at the same moment gets RUN_STATE_INVALID.
    if await run_repository.mark_applied_to_source(ctx.pool, run_id, user["id"]) is None:
        return _state_invalid("apply this run to your folder (only a succeeded run, once)")
    edits = await attempt_repository.find_successful_edits(ctx.pool, run_id)
    try:
        await asyncio.to_thread(_apply_to_folder, Path(run["source_reference"]), edits,
                                ctx.settings.max_edits_per_attempt, ctx.settings.max_changed_lines)
    except EditError as exc:
        await run_repository.clear_applied_to_source(ctx.pool, run_id)
        logger.info("Run %s: apply to source refused (%s problems)", run_id, len(exc.problems))
        return Result.failure("SOURCE_CHANGED", "Your folder changed since the run, so nothing was written. "
                                                "Download the patch instead.")
    except OSError as exc:
        await run_repository.clear_applied_to_source(ctx.pool, run_id)
        return Result.failure("SOURCE_NOT_WRITABLE", f"A file in your folder could not be written ({exc.strerror}), so "
                                                     "nothing was changed.")
    run = await run_repository.find_by_id_and_user(ctx.pool, run_id, user["id"])
    return Result.success(public_run(run))
