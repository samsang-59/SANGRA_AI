"""EventRepository (run_events): the run's diary, numbered 1, 2, 3 ... per run.

insert_event() is also used INSIDE other repository transactions, so a status change and its
status_changed event are saved together (all or nothing).
"""

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

FINAL_STATUSES = ("succeeded", "failed", "stopped")


async def insert_event(
    conn: AsyncConnection,
    run_id: int,
    event_type: str,
    message: str,
    details: dict | None = None,
    attempt_id: int | None = None,
    allow_after_final: bool = False,
) -> dict | None:
    """Must run inside a transaction. Locks the run row first, so two writers can never take the same
    number. Refuses (None) once the run has a final status, so the final status_changed event stays the
    last event of the run (only the finishing step itself passes allow_after_final)."""
    cur = await conn.execute("SELECT status FROM runs WHERE id = %s FOR UPDATE", (run_id,))
    run = await cur.fetchone()
    if run is None or (run["status"] in FINAL_STATUSES and not allow_after_final):
        return None
    cur = await conn.execute(
        "INSERT INTO run_events (run_id, attempt_id, sequence_number, event_type, message, details) "
        "SELECT %s, %s, COALESCE(MAX(sequence_number), 0) + 1, %s, %s, %s FROM run_events WHERE run_id = %s "
        "RETURNING id, sequence_number, event_type, message, details, attempt_id, created_at",
        (run_id, attempt_id, event_type, message, Jsonb(details) if details is not None else None, run_id),
    )
    return await cur.fetchone()


async def add_event(
    pool: AsyncConnectionPool,
    run_id: int,
    attempt_id: int | None,
    event_type: str,
    message: str,
    details: dict | None = None,
) -> dict | None:
    """Adds one event in its own transaction. None if the run is gone or already finished."""
    async with pool.connection() as conn:
        async with conn.transaction():
            return await insert_event(conn, run_id, event_type, message, details, attempt_id)


async def list_after(pool: AsyncConnectionPool, run_id: int, after_sequence: int, limit: int) -> list[dict]:
    """Events after a number (live stream, run detail)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT sequence_number, event_type, message, details, attempt_id, created_at FROM run_events "
            "WHERE run_id = %s AND sequence_number > %s ORDER BY sequence_number LIMIT %s",
            (run_id, after_sequence, limit),
        )
        return await cur.fetchall()


async def poll_for_stream(
    pool: AsyncConnectionPool, run_id: int, after_sequence: int, limit: int
) -> tuple[str | None, list[dict]]:
    """ONE query per poll of the live stream: the run's status (None if the run is gone) and the events
    after a number. The connection is given back at once, so a waiting stream holds no connection."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT r.status, e.sequence_number, e.event_type, e.message, e.details, e.attempt_id, e.created_at "
            "FROM runs r LEFT JOIN LATERAL ("
            "  SELECT * FROM run_events WHERE run_id = r.id AND sequence_number > %s "
            "  ORDER BY sequence_number LIMIT %s"
            ") e ON true WHERE r.id = %s ORDER BY e.sequence_number",
            (after_sequence, limit, run_id),
        )
        rows = await cur.fetchall()
    if not rows:
        return None, []
    events = [
        {key: row[key] for key in ("sequence_number", "event_type", "message", "details", "attempt_id", "created_at")}
        for row in rows
        if row["sequence_number"] is not None
    ]
    return rows[0]["status"], events


async def latest_hint(pool: AsyncConnectionPool, run_id: int) -> str | None:
    """The text of the newest hint the user sent (the agent reads it from the database, not from the resume value)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT details FROM run_events WHERE run_id = %s AND event_type = 'hint_received' "
            "ORDER BY sequence_number DESC LIMIT 1", (run_id,))
        row = await cur.fetchone()
        return (row["details"] or {}).get("hint") if row else None
