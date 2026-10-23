"""AttemptRepository (attempts, file_edits). Only SQL lives here; always parameterised."""

from psycopg_pool import AsyncConnectionPool

from app.repositories.event_repository import insert_event
from app.repositories.run_repository import RUN_COLUMNS, STATUS_MESSAGES, status_details

ATTEMPT_COLUMNS = (
    "id, run_id, attempt_number, reason_started, explanation, status, decision, decision_feedback, decided_at, "
    "applied_at, reverted_at, created_at, updated_at"
)
EDIT_COLUMNS = "id, attempt_id, file_path, start_line, end_line, old_code, new_code, edit_order, explanation"

_INSERT_ATTEMPT_SQL = (
    "INSERT INTO attempts (run_id, attempt_number, reason_started, explanation, status) "
    "SELECT %s, COALESCE(MAX(attempt_number), 0) + 1, %s, %s, 'proposed' FROM attempts WHERE run_id = %s "
    "RETURNING " + ATTEMPT_COLUMNS
)
_INSERT_EDIT_SQL = (
    "INSERT INTO file_edits (attempt_id, file_path, start_line, end_line, old_code, new_code, edit_order, explanation) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING " + EDIT_COLUMNS
)
_FIND_WAITING_SQL = (
    "SELECT " + ATTEMPT_COLUMNS + " FROM attempts WHERE run_id = %s AND status = 'waiting_for_approval' "
    "ORDER BY attempt_number DESC LIMIT 1"
)
_DECIDE_ATTEMPT_SQL = (
    "UPDATE attempts SET decision = %s, decision_feedback = %s, decided_at = now(), updated_at = now(), "
    "status = CASE WHEN %s = 'rejected' THEN 'rejected' ELSE status END "
    "WHERE id = %s AND run_id = %s AND status = 'waiting_for_approval' AND decision IS NULL RETURNING " + ATTEMPT_COLUMNS
)
_DECIDE_RUN_SQL = (
    "UPDATE runs SET status = %s, reject_count = reject_count + %s, updated_at = now() "
    "WHERE id = %s RETURNING " + RUN_COLUMNS
)
_LIST_ATTEMPTS_SQL = "SELECT " + ATTEMPT_COLUMNS + " FROM attempts WHERE run_id = %s ORDER BY attempt_number"
_LIST_EDITS_SQL = (
    "SELECT " + EDIT_COLUMNS + " FROM file_edits WHERE attempt_id = ANY(%s) ORDER BY attempt_id, edit_order"
)


async def create_attempt_with_edits(
    pool: AsyncConnectionPool, run_id: int, reason: str, explanation: str, edits: list[dict]
) -> dict:
    """ONE transaction: takes the next attempt number (the run row is locked, so two writers never share a
    number), inserts the attempt (status proposed) and all its edits in the given order."""
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT id FROM runs WHERE id = %s FOR UPDATE", (run_id,))
            cur = await conn.execute(_INSERT_ATTEMPT_SQL, (run_id, reason, explanation, run_id))
            attempt = await cur.fetchone()
            saved = []
            for order, edit in enumerate(edits, start=1):
                cur = await conn.execute(_INSERT_EDIT_SQL, (
                    attempt["id"], edit["file_path"], edit["start_line"], edit["end_line"], edit["old_code"],
                    edit["new_code"], order, edit.get("explanation")))
                saved.append(await cur.fetchone())
            return {**attempt, "edits": saved}


async def request_approval(pool: AsyncConnectionPool, run_id: int, attempt_id: int) -> dict | None:
    """Agent side, ONE transaction: the attempt proposed -> waiting_for_approval, the run searching ->
    waiting_for_approval, then the status_changed and approval_requested events. None if the run is not
    searching any more (for example it was cancelled)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE runs SET status = 'waiting_for_approval', updated_at = now() "
                "WHERE id = %s AND status = 'searching' RETURNING id", (run_id,))
            if await cur.fetchone() is None:
                return None
            cur = await conn.execute(
                "UPDATE attempts SET status = 'waiting_for_approval', updated_at = now() "
                "WHERE id = %s AND run_id = %s AND status = 'proposed' RETURNING id, attempt_number",
                (attempt_id, run_id))
            attempt = await cur.fetchone()
            if attempt is None:
                raise ValueError("the attempt is not a proposal of this run")
            cur = await conn.execute("SELECT count(*) AS n FROM file_edits WHERE attempt_id = %s", (attempt_id,))
            edit_count = (await cur.fetchone())["n"]
            await insert_event(conn, run_id, "status_changed", STATUS_MESSAGES["waiting_for_approval"],
                               status_details("waiting_for_approval"))
            return await insert_event(
                conn, run_id, "approval_requested",
                f"A fix is ready (attempt {attempt['attempt_number']}, {edit_count} edits). Please accept or reject it.",
                {"attempt_number": attempt["attempt_number"], "edit_count": edit_count}, attempt_id)


async def find_waiting_attempt(pool: AsyncConnectionPool, run_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(_FIND_WAITING_SQL, (run_id,))
        return await cur.fetchone()


async def record_approval_decision(
    pool: AsyncConnectionPool, run_id: int, attempt_id: int, decision: str, feedback: str | None
) -> dict:
    """ONE transaction, only if BOTH the run and the attempt are waiting: saves the decision, moves the run
    (applying on accept, searching on reject), adds 1 to reject_count on reject, adds the events.
    Returns {"outcome": "ok", "run", "attempt"} | {"outcome": "run_not_waiting"} | {"outcome": "stale_attempt"}.
    A double click or two tabs cannot both win: the run row is locked and the updates are conditional."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute("SELECT status FROM runs WHERE id = %s FOR UPDATE", (run_id,))
            run = await cur.fetchone()
            if run is None or run["status"] != "waiting_for_approval":
                return {"outcome": "run_not_waiting"}
            cur = await conn.execute(_DECIDE_ATTEMPT_SQL, (decision, feedback, decision, attempt_id, run_id))
            attempt = await cur.fetchone()
            if attempt is None:
                return {"outcome": "stale_attempt"}
            to_status = "applying" if decision == "accepted" else "searching"
            cur = await conn.execute(_DECIDE_RUN_SQL, (to_status, 1 if decision == "rejected" else 0, run_id))
            updated_run = await cur.fetchone()
            message = "You accepted the fix." if decision == "accepted" else "You rejected the fix."
            await insert_event(conn, run_id, "approval_decided", message,
                               {"decision": decision, "attempt_number": attempt["attempt_number"]}, attempt_id)
            await insert_event(conn, run_id, "status_changed", STATUS_MESSAGES[to_status], status_details(to_status))
            return {"outcome": "ok", "run": updated_run, "attempt": attempt}


async def list_with_edits(pool: AsyncConnectionPool, run_id: int) -> list[dict]:
    """All attempts of a run with their edits (run detail, package)."""
    async with pool.connection() as conn:
        cur = await conn.execute(_LIST_ATTEMPTS_SQL, (run_id,))
        attempts = await cur.fetchall()
        if not attempts:
            return []
        cur = await conn.execute(_LIST_EDITS_SQL, ([a["id"] for a in attempts],))
        edits = await cur.fetchall()
    by_attempt: dict[int, list[dict]] = {}
    for edit in edits:
        by_attempt.setdefault(edit["attempt_id"], []).append(edit)
    return [{**attempt, "edits": by_attempt.get(attempt["id"], [])} for attempt in attempts]


_MARK_STATUS_SQL = {
    "applied": "UPDATE attempts SET status = 'applied', applied_at = now(), updated_at = now() "
               "WHERE id = %s RETURNING " + ATTEMPT_COLUMNS,
    "tests_passed": "UPDATE attempts SET status = 'tests_passed', updated_at = now() WHERE id = %s RETURNING "
                    + ATTEMPT_COLUMNS,
    "tests_failed": "UPDATE attempts SET status = 'tests_failed', updated_at = now() WHERE id = %s RETURNING "
                    + ATTEMPT_COLUMNS,
}
_FIND_ATTEMPT_SQL = "SELECT " + ATTEMPT_COLUMNS + " FROM attempts WHERE id = %s AND run_id = %s"
_EDITS_OF_SQL = "SELECT " + EDIT_COLUMNS + " FROM file_edits WHERE attempt_id = %s ORDER BY edit_order"


async def mark_attempt_status(pool: AsyncConnectionPool, attempt_id: int, status: str) -> dict | None:
    """applied (records applied_at) / tests_passed / tests_failed."""
    async with pool.connection() as conn:
        cur = await conn.execute(_MARK_STATUS_SQL[status], (attempt_id,))
        return await cur.fetchone()


async def set_reverted_at(pool: AsyncConnectionPool, attempt_id: int) -> None:
    """Records an undo (the attempt keeps its status tests_failed)."""
    async with pool.connection() as conn:
        await conn.execute("UPDATE attempts SET reverted_at = now(), updated_at = now() WHERE id = %s", (attempt_id,))


async def find_with_edits(pool: AsyncConnectionPool, run_id: int, attempt_id: int) -> dict | None:
    """One attempt of a run with its edits (in their saved order)."""
    async with pool.connection() as conn:
        cur = await conn.execute(_FIND_ATTEMPT_SQL, (attempt_id, run_id))
        attempt = await cur.fetchone()
        if attempt is None:
            return None
        cur = await conn.execute(_EDITS_OF_SQL, (attempt_id,))
        return {**attempt, "edits": await cur.fetchall()}


_SUCCESSFUL_EDITS_SQL = (
    "SELECT e.id, e.attempt_id, e.file_path, e.start_line, e.end_line, e.old_code, e.new_code, e.edit_order, "
    "e.explanation FROM file_edits e JOIN attempts a ON a.id = e.attempt_id "
    "WHERE a.run_id = %s AND a.status = 'tests_passed' ORDER BY e.edit_order"
)


async def find_successful_edits(pool: AsyncConnectionPool, run_id: int) -> list[dict]:
    """The edits of the attempt that passed the tests ("Apply to my folder")."""
    async with pool.connection() as conn:
        cur = await conn.execute(_SUCCESSFUL_EDITS_SQL, (run_id,))
        return await cur.fetchall()
