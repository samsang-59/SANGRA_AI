"""PackageRepository (packages). create(), find_expired() and mark_deleted() come with the finalizer (B10)."""

from psycopg_pool import AsyncConnectionPool


async def find_by_run(pool: AsyncConnectionPool, run_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, run_id, storage_path, size_bytes, expires_at, deleted_at, created_at "
            "FROM packages WHERE run_id = %s",
            (run_id,),
        )
        return await cur.fetchone()
