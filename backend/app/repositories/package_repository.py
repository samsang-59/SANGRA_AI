"""PackageRepository (packages)."""

from psycopg_pool import AsyncConnectionPool

PACKAGE_COLUMNS = "id, run_id, storage_path, size_bytes, expires_at, deleted_at, created_at"
_CREATE_SQL = (
    "INSERT INTO packages (run_id, storage_path, size_bytes, expires_at) "
    "VALUES (%s, %s, %s, now() + make_interval(days => %s)) ON CONFLICT (run_id) DO NOTHING RETURNING "
    + PACKAGE_COLUMNS
)
_FIND_SQL = "SELECT " + PACKAGE_COLUMNS + ", expires_at <= now() AS expired FROM packages WHERE run_id = %s"
_EXPIRED_SQL = (
    "SELECT " + PACKAGE_COLUMNS + " FROM packages WHERE deleted_at IS NULL AND expires_at <= now() "
    "ORDER BY expires_at LIMIT %s"
)


async def create(pool: AsyncConnectionPool, run_id: int, storage_path: str, size_bytes: int, expiry_days: int
                 ) -> dict | None:
    """Adds the package row (expires `expiry_days` after now, by the database clock). None if the run already has a
    package (one package per run)."""
    async with pool.connection() as conn:
        cur = await conn.execute(_CREATE_SQL, (run_id, storage_path, size_bytes, expiry_days))
        return await cur.fetchone()


async def find_by_run(pool: AsyncConnectionPool, run_id: int) -> dict | None:
    """The package of a run, with `expired` decided by the database clock."""
    async with pool.connection() as conn:
        cur = await conn.execute(_FIND_SQL, (run_id,))
        return await cur.fetchone()


async def find_expired(pool: AsyncConnectionPool, limit: int) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(_EXPIRED_SQL, (limit,))
        return await cur.fetchall()


async def mark_deleted(pool: AsyncConnectionPool, package_ids: list[int]) -> int:
    """Sets deleted_at on these packages (one statement for a whole batch)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE packages SET deleted_at = now() WHERE id = ANY(%s) AND deleted_at IS NULL", (package_ids,))
        return cur.rowcount


async def live_storage_paths(pool: AsyncConnectionPool) -> set[str]:
    """Every package file the database still points to (for the orphan file cleanup)."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT storage_path FROM packages WHERE deleted_at IS NULL")
        return {row["storage_path"] for row in await cur.fetchall()}
