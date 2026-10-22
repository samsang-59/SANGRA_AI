"""TestResultRepository (test_results). create() and find_baseline() come with the test runner (B7/B9)."""

from psycopg_pool import AsyncConnectionPool


async def list_by_run(pool: AsyncConnectionPool, run_id: int) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, attempt_id, test_type, status, exit_code, log_excerpt, duration_ms, docker_image, "
            "started_at, finished_at, created_at FROM test_results WHERE run_id = %s ORDER BY id",
            (run_id,),
        )
        return await cur.fetchall()
