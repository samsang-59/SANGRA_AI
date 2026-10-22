"""UserRepository (users, user_tokens). Only SQL lives here; always parameterised."""

from psycopg_pool import AsyncConnectionPool


async def find_by_id(pool: AsyncConnectionPool, user_id: int) -> dict | None:
    """For the login check: includes is_email_verified and password_changed_at."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, name, email, password_hash, is_email_verified, password_changed_at, created_at, updated_at "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        return await cur.fetchone()
