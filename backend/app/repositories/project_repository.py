"""ProjectRepository (projects). Only SQL lives here; always parameterised; every query filters by user."""

from psycopg_pool import AsyncConnectionPool

ACTIVE_STATUSES = ("started", "searching", "waiting_for_hint", "waiting_for_approval", "applying", "testing")


async def create_if_under_limit(pool: AsyncConnectionPool, user_id: int, data: dict, limit: int) -> dict | None:
    """ONE transaction: locks the user's row, counts the user's projects, inserts only if under the limit.
    The lock makes a second simultaneous request wait and count AFTER the first one. None = limit reached."""
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
            cur = await conn.execute("SELECT count(*) AS n FROM projects WHERE user_id = %s", (user_id,))
            if (await cur.fetchone())["n"] >= limit:
                return None
            cur = await conn.execute(
                "INSERT INTO projects (user_id, name, language, source_type, source_reference, branch, test_command) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, name, language, source_type, source_reference, branch, test_command, created_at, updated_at",
                (user_id, data["name"], data["language"], data["source_type"], data["source_reference"],
                 data["branch"], data["test_command"]),
            )
            return await cur.fetchone()


async def list_by_user(pool: AsyncConnectionPool, user_id: int, limit: int, offset: int) -> tuple[list[dict], int]:
    """The user's projects, newest first, plus the total count."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, name, language, source_type, source_reference, branch, test_command, created_at, updated_at "
            "FROM projects WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            (user_id, limit, offset),
        )
        rows = await cur.fetchall()
        cur = await conn.execute("SELECT count(*) AS n FROM projects WHERE user_id = %s", (user_id,))
        return rows, (await cur.fetchone())["n"]


async def find_by_id_and_user(pool: AsyncConnectionPool, project_id: int, user_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, name, language, source_type, source_reference, branch, test_command, created_at, updated_at "
            "FROM projects WHERE id = %s AND user_id = %s",
            (project_id, user_id),
        )
        return await cur.fetchone()


# The columns a PATCH may change: a FIXED list. Each one is written only when its flag is true, so the SQL
# text never changes and only values are parameters.
UPDATABLE_COLUMNS = ("name", "language", "source_type", "source_reference", "branch", "test_command")


async def update(pool: AsyncConnectionPool, project_id: int, user_id: int, changed_fields: dict) -> dict | None:
    """Saves only the changed fields and sets updated_at. None if the project is not there (or not theirs)."""
    unknown = set(changed_fields) - set(UPDATABLE_COLUMNS)
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    params: dict = {"id": project_id, "user_id": user_id}
    for column in UPDATABLE_COLUMNS:
        params[f"set_{column}"] = column in changed_fields
        params[column] = changed_fields.get(column)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE projects SET "
            "name = CASE WHEN %(set_name)s THEN %(name)s ELSE name END, "
            "language = CASE WHEN %(set_language)s THEN %(language)s ELSE language END, "
            "source_type = CASE WHEN %(set_source_type)s THEN %(source_type)s ELSE source_type END, "
            "source_reference = CASE WHEN %(set_source_reference)s THEN %(source_reference)s ELSE source_reference END, "
            "branch = CASE WHEN %(set_branch)s THEN %(branch)s ELSE branch END, "
            "test_command = CASE WHEN %(set_test_command)s THEN %(test_command)s ELSE test_command END, "
            "updated_at = now() "
            "WHERE id = %(id)s AND user_id = %(user_id)s "
            "RETURNING id, name, language, source_type, source_reference, branch, test_command, created_at, updated_at",
            params,
        )
        return await cur.fetchone()


async def delete_with_runs(pool: AsyncConnectionPool, project_id: int, user_id: int) -> dict:
    """ONE transaction: locks the project row (nobody can start a run on it meanwhile), refuses if it has an
    active run, collects the run ids and package files, deletes (the database cascades to the runs).
    Returns {"outcome": "not_found" | "has_active_run" | "deleted", "run_ids": [...], "package_paths": [...]}."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT id FROM projects WHERE id = %s AND user_id = %s FOR UPDATE", (project_id, user_id))
            if await cur.fetchone() is None:
                return {"outcome": "not_found", "run_ids": [], "package_paths": []}
            cur = await conn.execute(
                "SELECT 1 FROM runs WHERE project_id = %s AND status = ANY(%s) LIMIT 1",
                (project_id, list(ACTIVE_STATUSES)),
            )
            if await cur.fetchone() is not None:
                return {"outcome": "has_active_run", "run_ids": [], "package_paths": []}
            cur = await conn.execute("SELECT id FROM runs WHERE project_id = %s ORDER BY id", (project_id,))
            run_ids = [row["id"] for row in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT p.storage_path FROM packages p JOIN runs r ON r.id = p.run_id "
                "WHERE r.project_id = %s AND p.deleted_at IS NULL",
                (project_id,),
            )
            package_paths = [row["storage_path"] for row in await cur.fetchall()]
            await conn.execute("DELETE FROM projects WHERE id = %s AND user_id = %s", (project_id, user_id))
            return {"outcome": "deleted", "run_ids": run_ids, "package_paths": package_paths}
