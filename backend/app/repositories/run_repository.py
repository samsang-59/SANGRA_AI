"""RunRepository (runs). Only SQL lives here; always parameterised.

Functions named *_and_user are for routes (they filter by the user). find_by_id and the job queries are
for background code only (agent, jobs). Every status change also writes its status_changed event in the
SAME transaction (insert_event), so the diary always matches the run.
"""

from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from app.repositories.event_repository import FINAL_STATUSES, insert_event

ACTIVE_STATUSES = ("started", "searching", "waiting_for_hint", "waiting_for_approval", "applying", "testing")
WAITING_STATUSES = ("waiting_for_hint", "waiting_for_approval")
INTERRUPTED_STATUSES = ("started", "searching", "applying", "testing")

RUN_COLUMNS = (
    "id, user_id, project_id, bug_description, status, language, test_command, branch, source_type, "
    "source_reference, source_commit, llm_model, workspace_path, workspace_deleted_at, stop_reason, reject_count, "
    "failed_test_count, hint_count, final_summary, total_tokens, applied_to_source_at, started_at, finished_at, "
    "created_at, updated_at"
)


# Full statements, composed ONCE from the fixed column list above (never from request values).
_INSERT_RUN_SQL = (
    "INSERT INTO runs (user_id, project_id, bug_description, status, language, test_command, branch, "
    "source_type, source_reference, llm_model) "
    "VALUES (%s, %s, %s, 'started', %s, %s, %s, %s, %s, %s) RETURNING " + RUN_COLUMNS
)
_FIND_BY_ID_AND_USER_SQL = "SELECT " + RUN_COLUMNS + " FROM runs WHERE id = %s AND user_id = %s"
_FIND_BY_ID_SQL = "SELECT " + RUN_COLUMNS + " FROM runs WHERE id = %s"
_LIST_WHERE = (
    "WHERE r.user_id = %(user_id)s "
    "AND (%(project_id)s::bigint IS NULL OR r.project_id = %(project_id)s) "
    "AND (%(statuses)s::text[] IS NULL OR r.status = ANY(%(statuses)s)) "
)
_LIST_SQL = (
    "SELECT r.id, r.project_id, p.name AS project_name, r.bug_description, r.status, r.stop_reason, "
    "r.language, r.source_type, r.reject_count, r.failed_test_count, r.hint_count, r.started_at, "
    "r.finished_at, r.created_at, r.updated_at "
    "FROM runs r JOIN projects p ON p.id = r.project_id " + _LIST_WHERE +
    "ORDER BY r.created_at DESC, r.id DESC LIMIT %(limit)s OFFSET %(offset)s"
)
_COUNT_SQL = "SELECT count(*) AS n FROM runs r " + _LIST_WHERE
_TRANSITION_SQL = (
    "UPDATE runs SET status = %s, updated_at = now() WHERE id = %s AND status = ANY(%s) RETURNING " + RUN_COLUMNS
)
_REQUEST_HINT_SQL = (
    "UPDATE runs SET status = 'waiting_for_hint', hint_count = hint_count + 1, updated_at = now() "
    "WHERE id = %s RETURNING " + RUN_COLUMNS
)
_RECORD_HINT_SQL = (
    "UPDATE runs SET status = 'searching', updated_at = now() "
    "WHERE id = %s AND status = 'waiting_for_hint' RETURNING " + RUN_COLUMNS
)
_FINISH_SQL = (
    "UPDATE runs SET status = %s, stop_reason = %s, final_summary = %s, finished_at = now(), "
    "updated_at = now() WHERE id = %s AND status = ANY(%s) RETURNING " + RUN_COLUMNS
)


def status_details(status: str, stop_reason: str | None = None) -> dict:
    details = {"status": status}
    if status in FINAL_STATUSES:
        details["stop_reason"] = stop_reason
    return details


STATUS_MESSAGES = {
    "started": "Run started.",
    "searching": "Searching the code.",
    "waiting_for_hint": "Waiting for a hint from you.",
    "waiting_for_approval": "Waiting for your approval.",
    "applying": "Applying the approved changes.",
    "testing": "Running the tests.",
    "succeeded": "Run finished: the tests passed.",
    "failed": "Run finished: failed.",
    "stopped": "Run finished: stopped.",
}


async def create_run(pool: AsyncConnectionPool, user_id: int, project: dict, bug_description: str, llm_model: str
                     ) -> dict:
    """Inserts the run (status started, a snapshot of the project settings) and its first status_changed event.
    The one-active-run rule is the database's partial unique index: no check-then-insert.
    Returns {"outcome": "created", "run": row} | {"outcome": "active_run_exists"} | {"outcome": "project_gone"}."""
    async with pool.connection() as conn:
        try:
            async with conn.transaction():
                cur = await conn.execute(
                    _INSERT_RUN_SQL,
                    (user_id, project["id"], bug_description, project["language"], project["test_command"],
                     project["branch"], project["source_type"], project["source_reference"], llm_model),
                )
                run = await cur.fetchone()
                await insert_event(conn, run["id"], "status_changed", STATUS_MESSAGES["started"],
                                   status_details("started"))
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "runs_one_active_per_user":
                return {"outcome": "active_run_exists"}
            raise
        except errors.ForeignKeyViolation:
            # The project was deleted at the same moment (the delete held the project row lock).
            return {"outcome": "project_gone"}
        return {"outcome": "created", "run": run}


async def find_by_id_and_user(pool: AsyncConnectionPool, run_id: int, user_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(_FIND_BY_ID_AND_USER_SQL, (run_id, user_id))
        return await cur.fetchone()


async def find_by_id(pool: AsyncConnectionPool, run_id: int) -> dict | None:
    """Background code only (agent, jobs), never routes."""
    async with pool.connection() as conn:
        cur = await conn.execute(_FIND_BY_ID_SQL, (run_id,))
        return await cur.fetchone()


async def list_by_user(
    pool: AsyncConnectionPool, user_id: int, project_id: int | None, statuses: list[str] | None, limit: int, offset: int
) -> tuple[list[dict], int]:
    """History list: the user's runs, newest first, optional project and status filters, plus the total."""
    params = {"user_id": user_id, "project_id": project_id, "statuses": statuses, "limit": limit, "offset": offset}
    async with pool.connection() as conn:
        cur = await conn.execute(_LIST_SQL, params)
        rows = await cur.fetchall()
        cur = await conn.execute(_COUNT_SQL, params)
        return rows, (await cur.fetchone())["n"]


async def transition_status(pool: AsyncConnectionPool, run_id: int, allowed_from: tuple[str, ...], to_status: str
                            ) -> dict | None:
    """Changes the status ONLY IF the current status is in allowed_from (the guard against races: of two
    simultaneous changes only one wins; the other gets None). Adds the status_changed event."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(_TRANSITION_SQL, (to_status, run_id, list(allowed_from)))
            run = await cur.fetchone()
            if run is not None:
                await insert_event(conn, run_id, "status_changed", STATUS_MESSAGES[to_status], status_details(to_status))
            return run


_INCREMENT_SQL = {
    "reject_count": "UPDATE runs SET reject_count = reject_count + 1, updated_at = now() WHERE id = %s "
                    "RETURNING reject_count AS value",
    "failed_test_count": "UPDATE runs SET failed_test_count = failed_test_count + 1, updated_at = now() WHERE id = %s "
                         "RETURNING failed_test_count AS value",
    "hint_count": "UPDATE runs SET hint_count = hint_count + 1, updated_at = now() WHERE id = %s "
                  "RETURNING hint_count AS value",
}


async def increment_counter(pool: AsyncConnectionPool, run_id: int, counter: str) -> int | None:
    """+1 on reject_count / failed_test_count / hint_count in one statement (the name comes from a fixed list).
    Returns the new value."""
    sql = _INCREMENT_SQL[counter]
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (run_id,))
        row = await cur.fetchone()
        return row["value"] if row else None


async def request_hint(pool: AsyncConnectionPool, run_id: int, max_hints: int) -> dict:
    """Agent side, ONE transaction: if hint_count already reached the limit -> {"outcome": "limit_reached"};
    else hint_count + 1, searching -> waiting_for_hint, the status_changed and hint_requested events.
    {"outcome": "state_invalid"} if the run is not searching (for example it was cancelled)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute("SELECT status, hint_count FROM runs WHERE id = %s FOR UPDATE", (run_id,))
            run = await cur.fetchone()
            if run is None or run["status"] != "searching":
                return {"outcome": "state_invalid"}
            if run["hint_count"] >= max_hints:
                return {"outcome": "limit_reached"}
            cur = await conn.execute(_REQUEST_HINT_SQL, (run_id,))
            updated = await cur.fetchone()
            await insert_event(conn, run_id, "status_changed", STATUS_MESSAGES["waiting_for_hint"],
                               status_details("waiting_for_hint"))
            await insert_event(conn, run_id, "hint_requested",
                               f"I could not find the bug. Please give a hint ({updated['hint_count']} of {max_hints}).",
                               {"hint_number": updated["hint_count"], "max_hints": max_hints})
            return {"outcome": "ok", "run": updated}


async def record_hint(pool: AsyncConnectionPool, run_id: int, text: str) -> dict | None:
    """Route side, ONE conditional transaction: only if the run is waiting_for_hint, save the hint as an
    event and move the run back to searching. None if the run was not waiting (double click, other tab)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(_RECORD_HINT_SQL, (run_id,))
            run = await cur.fetchone()
            if run is None:
                return None
            await insert_event(conn, run_id, "hint_received", "Hint received.", {"hint": text})
            await insert_event(conn, run_id, "status_changed", STATUS_MESSAGES["searching"], status_details("searching"))
            return run


async def finish_run(
    pool: AsyncConnectionPool, run_id: int, final_status: str, stop_reason: str | None, final_summary: str | None
) -> dict | None:
    """Sets the final status, stop reason, summary and finished_at, ONLY IF the run is not finished yet, and
    adds the final status_changed event (always the last event of the run). None if it was already finished
    (a cancel and the agent's own finish at the same moment: exactly one wins)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                _FINISH_SQL, (final_status, stop_reason, final_summary, run_id, list(ACTIVE_STATUSES)))
            run = await cur.fetchone()
            if run is not None:
                await insert_event(conn, run_id, "status_changed", STATUS_MESSAGES[final_status],
                                   status_details(final_status, stop_reason), allow_after_final=True)
            return run


async def delete_run(pool: AsyncConnectionPool, run_id: int, user_id: int) -> dict:
    """ONE transaction: locks the run, refuses if active, collects the package path, deletes (the database
    cascades to attempts, edits, events, results and the package row).
    Returns {"outcome": "not_found" | "active" | "deleted", "package_paths": [...]}."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute("SELECT status FROM runs WHERE id = %s AND user_id = %s FOR UPDATE",
                                     (run_id, user_id))
            run = await cur.fetchone()
            if run is None:
                return {"outcome": "not_found", "package_paths": []}
            if run["status"] in ACTIVE_STATUSES:
                return {"outcome": "active", "package_paths": []}
            cur = await conn.execute(
                "SELECT storage_path FROM packages WHERE run_id = %s AND deleted_at IS NULL", (run_id,))
            paths = [row["storage_path"] for row in await cur.fetchall()]
            await conn.execute("DELETE FROM runs WHERE id = %s AND user_id = %s", (run_id, user_id))
            return {"outcome": "deleted", "package_paths": paths}


async def find_stuck_waiting(pool: AsyncConnectionPool, older_than_seconds: int) -> list[dict]:
    """Runs waiting for approval or a hint longer than the limit (timeout job). While a run waits nothing
    else touches it, so updated_at is the moment it started waiting."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, status FROM runs WHERE status = ANY(%s) "
            "AND updated_at < now() - make_interval(secs => %s) ORDER BY id",
            (list(WAITING_STATUSES), older_than_seconds),
        )
        return await cur.fetchall()


async def find_interrupted_runs(pool: AsyncConnectionPool, created_before) -> list[dict]:
    """Runs left in started/searching/applying/testing (startup recovery: the graph was running when the
    server stopped, so nothing can continue them). Only runs created before this server process started."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, status FROM runs WHERE status = ANY(%s) AND created_at < %s ORDER BY id",
            (list(INTERRUPTED_STATUSES), created_before))
        return await cur.fetchall()


async def set_workspace_ready(pool: AsyncConnectionPool, run_id: int, workspace_path: str, source_commit: str | None
                              ) -> None:
    """Records where the copy lives and, for GitHub, the exact commit that was cloned."""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE runs SET workspace_path = %s, source_commit = %s, updated_at = now() WHERE id = %s",
            (workspace_path, source_commit, run_id))


async def add_tokens(pool: AsyncConnectionPool, run_id: int, tokens: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE runs SET total_tokens = COALESCE(total_tokens, 0) + %s WHERE id = %s", (tokens, run_id))


async def mark_workspace_deleted(pool: AsyncConnectionPool, run_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE runs SET workspace_deleted_at = now() WHERE id = %s AND workspace_deleted_at IS NULL", (run_id,))


_CLAIM_APPLY_SQL = (
    "UPDATE runs SET applied_to_source_at = now(), updated_at = now() "
    "WHERE id = %s AND user_id = %s AND status = 'succeeded' AND applied_to_source_at IS NULL RETURNING " + RUN_COLUMNS
)


async def mark_applied_to_source(pool: AsyncConnectionPool, run_id: int, user_id: int) -> dict | None:
    """Sets applied_to_source_at, only if the run succeeded and it is still empty (one conditional update, so two
    clicks cannot both apply). None if not allowed."""
    async with pool.connection() as conn:
        cur = await conn.execute(_CLAIM_APPLY_SQL, (run_id, user_id))
        return await cur.fetchone()


async def clear_applied_to_source(pool: AsyncConnectionPool, run_id: int) -> None:
    """Gives the claim back when applying failed (nothing was written)."""
    async with pool.connection() as conn:
        await conn.execute("UPDATE runs SET applied_to_source_at = NULL WHERE id = %s", (run_id,))


async def find_old_workspaces(pool: AsyncConnectionPool, older_than_seconds: float, limit: int) -> list[dict]:
    """Finished runs whose temporary copy was not deleted and is older than the limit (workspace cleanup)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, user_id, workspace_path FROM runs WHERE workspace_path IS NOT NULL "
            "AND workspace_deleted_at IS NULL AND status = ANY(%s) "
            "AND created_at < now() - make_interval(secs => %s) ORDER BY id LIMIT %s",
            (list(FINAL_STATUSES), older_than_seconds, limit))
        return await cur.fetchall()


_UNFINISHED_CLEANUPS_SQL = (
    "SELECT " + RUN_COLUMNS + " FROM runs WHERE workspace_path IS NOT NULL AND workspace_deleted_at IS NULL "
    "AND status = ANY(%s) ORDER BY id"
)


async def find_unfinished_cleanups(pool: AsyncConnectionPool) -> list[dict]:
    """Finished runs whose finish steps did not complete (the server stopped in the middle): the copy still exists.
    Startup recovery completes them."""
    async with pool.connection() as conn:
        cur = await conn.execute(_UNFINISHED_CLEANUPS_SQL, (list(FINAL_STATUSES),))
        return await cur.fetchall()


async def active_run_ids(pool: AsyncConnectionPool) -> set[int]:
    """The ids of every run still working or waiting (their temporary copies are never removed by the cleanup)."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT id FROM runs WHERE status = ANY(%s)", (list(ACTIVE_STATUSES),))
        return {row["id"] for row in await cur.fetchall()}
