"""ProjectService (Layer 4). Every lookup also filters by the user's id; not yours = NOT_FOUND."""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.result import Result
from app.repositories import project_repository
from app.services.cleanup_service import RunCleanup
from app.validators.project_rules import check_reference

NOT_FOUND_MESSAGE = "Project not found."


@dataclass(frozen=True)
class ProjectContext:
    pool: AsyncConnectionPool
    settings: Settings
    cleanup: RunCleanup


def _folder_is_readable(path: str) -> bool:
    folder = Path(path)
    try:
        if not folder.is_dir() or not os.access(folder, os.R_OK | os.X_OK):
            return False
        with os.scandir(folder) as entries:
            next(entries, None)
        return True
    except OSError:
        return False


async def _check_local_path(ctx: ProjectContext, source_type: str, source_reference: str | None) -> Result | None:
    if source_type != "local_path":
        return None
    if ctx.settings.app_mode != "local":
        return Result.failure("LOCAL_PATH_NOT_ALLOWED",
                              "Local folders can only be used when the backend runs on your own machine.")
    if not await asyncio.to_thread(_folder_is_readable, source_reference or ""):
        return Result.failure("PATH_NOT_FOUND", "The folder does not exist or cannot be read.")
    return None


async def create_project(ctx: ProjectContext, user: dict, data: dict) -> Result:
    if problem := await _check_local_path(ctx, data["source_type"], data["source_reference"]):
        return problem
    project = await project_repository.create_if_under_limit(ctx.pool, user["id"], data, ctx.settings.project_limit)
    if project is None:
        return Result.failure("PROJECT_LIMIT_REACHED",
                              f"You can have at most {ctx.settings.project_limit} projects. Delete one first.")
    return Result.success(project)


async def list_projects(ctx: ProjectContext, user: dict) -> Result:
    rows, total = await project_repository.list_by_user(ctx.pool, user["id"], ctx.settings.project_limit, 0)
    return Result.success({"items": rows, "total": total})


async def get_project(ctx: ProjectContext, user: dict, project_id: int) -> Result:
    project = await project_repository.find_by_id_and_user(ctx.pool, project_id, user["id"])
    if project is None:
        return Result.failure("NOT_FOUND", NOT_FOUND_MESSAGE)
    return Result.success(project)


async def update_project(ctx: ProjectContext, user: dict, project_id: int, changes: dict) -> Result:
    existing = await project_repository.find_by_id_and_user(ctx.pool, project_id, user["id"])
    if existing is None:
        return Result.failure("NOT_FOUND", NOT_FOUND_MESSAGE)
    if not changes:
        return Result.success(existing)

    merged = {**existing, **changes}
    try:
        merged_reference = check_reference(merged["source_type"], merged["source_reference"])
    except ValueError as exc:
        return Result.failure("INVALID_PROJECT_CONFIG", f"The project would not be valid: {exc}.")
    if merged_reference != merged["source_reference"]:  # a zip project keeps no reference
        changes = {**changes, "source_reference": merged_reference}
    if "source_type" in changes or "source_reference" in changes:
        if problem := await _check_local_path(ctx, merged["source_type"], merged_reference):
            return problem

    # Allowed during an active run: every run keeps its own snapshot of the settings.
    project = await project_repository.update(ctx.pool, project_id, user["id"], changes)
    if project is None:  # deleted meanwhile
        return Result.failure("NOT_FOUND", NOT_FOUND_MESSAGE)
    return Result.success(project)


async def delete_project(ctx: ProjectContext, user: dict, project_id: int) -> Result:
    outcome = await project_repository.delete_with_runs(ctx.pool, project_id, user["id"])
    if outcome["outcome"] == "not_found":
        return Result.failure("NOT_FOUND", NOT_FOUND_MESSAGE)
    if outcome["outcome"] == "has_active_run":
        return Result.failure("PROJECT_HAS_ACTIVE_RUN",
                              "This project has a run in progress. Cancel the run first, then delete the project.")
    # After the commit: files and LangGraph data. Failures are logged inside; the delete stands.
    await ctx.cleanup.after_runs_deleted(outcome["run_ids"], outcome["package_paths"])
    return Result.success({"id": project_id, "deleted": True})
