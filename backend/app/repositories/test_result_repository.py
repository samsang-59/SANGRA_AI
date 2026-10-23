"""TestResultRepository (test_results)."""

from datetime import datetime

from psycopg_pool import AsyncConnectionPool

RESULT_COLUMNS = ("id, run_id, attempt_id, test_type, status, exit_code, log_excerpt, duration_ms, docker_image, "
                  "started_at, finished_at, created_at")
_CREATE_SQL = (
    "INSERT INTO test_results (run_id, attempt_id, test_type, status, exit_code, log_excerpt, duration_ms, "
    "docker_image, started_at, finished_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING "
    + RESULT_COLUMNS
)
_LIST_SQL = "SELECT " + RESULT_COLUMNS + " FROM test_results WHERE run_id = %s ORDER BY id"
_BASELINE_SQL = "SELECT " + RESULT_COLUMNS + " FROM test_results WHERE run_id = %s AND test_type = 'baseline'"


async def create(pool: AsyncConnectionPool, run_id: int, attempt_id: int | None, test_type: str, status: str,
                 exit_code: int | None, log_excerpt: str, duration_ms: int, docker_image: str,
                 started_at: datetime, finished_at: datetime) -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute(_CREATE_SQL, (run_id, attempt_id, test_type, status, exit_code, log_excerpt,
                                               duration_ms, docker_image, started_at, finished_at))
        return await cur.fetchone()


async def list_by_run(pool: AsyncConnectionPool, run_id: int) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(_LIST_SQL, (run_id,))
        return await cur.fetchall()


async def find_baseline(pool: AsyncConnectionPool, run_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(_BASELINE_SQL, (run_id,))
        return await cur.fetchone()
