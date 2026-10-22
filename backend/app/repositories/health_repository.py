"""SQL used by the health check."""

from psycopg_pool import AsyncConnectionPool


async def ping(pool: AsyncConnectionPool) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1 AS ok")
        row = await cur.fetchone()
        return bool(row and row["ok"] == 1)
