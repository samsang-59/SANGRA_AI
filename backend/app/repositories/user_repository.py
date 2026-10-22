"""UserRepository (users, user_tokens). Only SQL lives here; always parameterised.

Expiry is always decided with the DATABASE clock (now()), so token lifetimes are passed in seconds and
added to now() inside the SQL.
"""

from psycopg import errors
from psycopg_pool import AsyncConnectionPool


async def find_by_email(pool: AsyncConnectionPool, email: str) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, name, email, password_hash, is_email_verified, password_changed_at, created_at, updated_at "
            "FROM users WHERE email = %s",
            (email,),
        )
        return await cur.fetchone()


async def find_by_id(pool: AsyncConnectionPool, user_id: int) -> dict | None:
    """For the login check: includes is_email_verified and password_changed_at."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, name, email, password_hash, is_email_verified, password_changed_at, created_at, updated_at "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        return await cur.fetchone()


async def create_user_with_verification_token(
    pool: AsyncConnectionPool, name: str, email: str, password_hash: str, token_hash: str, expires_in_seconds: int
) -> dict | None:
    """Inserts the user and the verification token hash in ONE transaction. None if the email already exists."""
    async with pool.connection() as conn:
        try:
            async with conn.transaction():
                cur = await conn.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s) "
                    "RETURNING id, name, email, password_hash, is_email_verified, password_changed_at, "
                    "created_at, updated_at",
                    (name, email, password_hash),
                )
                user = await cur.fetchone()
                await conn.execute(
                    "INSERT INTO user_tokens (user_id, token_type, token_hash, expires_at) "
                    "VALUES (%s, 'email_verification', %s, now() + make_interval(secs => %s))",
                    (user["id"], token_hash, expires_in_seconds),
                )
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "users_email_key":
                return None
            raise
        return user


async def create_token(
    pool: AsyncConnectionPool, user_id: int, token_type: str, token_hash: str, expires_in_seconds: int
) -> dict:
    """Inserts a token (resend, forgot-password)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO user_tokens (user_id, token_type, token_hash, expires_at) "
            "VALUES (%s, %s, %s, now() + make_interval(secs => %s)) "
            "RETURNING id, user_id, token_type, expires_at, used_at, created_at",
            (user_id, token_type, token_hash, expires_in_seconds),
        )
        return await cur.fetchone()


async def get_latest_token_time(pool: AsyncConnectionPool, user_id: int, token_type: str) -> dict | None:
    """When the newest token of that type was created, and how many seconds ago (by the database clock)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT created_at, EXTRACT(EPOCH FROM now() - created_at)::float AS age_seconds "
            "FROM user_tokens WHERE user_id = %s AND token_type = %s ORDER BY created_at DESC LIMIT 1",
            (user_id, token_type),
        )
        return await cur.fetchone()


async def verify_email_with_token(pool: AsyncConnectionPool, token_hash: str) -> dict | None:
    """ONE transaction: uses the token (only if unused and not expired) and marks the user verified.
    The conditional update lets only one of two simultaneous clicks win. None if bad, used or expired."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE user_tokens SET used_at = now() "
                "WHERE token_hash = %s AND token_type = 'email_verification' AND used_at IS NULL AND expires_at > now() "
                "RETURNING user_id",
                (token_hash,),
            )
            token = await cur.fetchone()
            if token is None:
                return None
            cur = await conn.execute(
                "UPDATE users SET is_email_verified = true, updated_at = now() WHERE id = %s "
                "RETURNING id, name, email, is_email_verified, created_at",
                (token["user_id"],),
            )
            return await cur.fetchone()


async def reset_password_with_token(pool: AsyncConnectionPool, token_hash: str, new_password_hash: str) -> int | None:
    """ONE transaction: uses the reset token, saves the new password, sets password_changed_at = now() and
    marks the user's other unused reset tokens used. Returns the user id, or None if bad, used or expired."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE user_tokens SET used_at = now() "
                "WHERE token_hash = %s AND token_type = 'password_reset' AND used_at IS NULL AND expires_at > now() "
                "RETURNING user_id",
                (token_hash,),
            )
            token = await cur.fetchone()
            if token is None:
                return None
            await conn.execute(
                "UPDATE users SET password_hash = %s, password_changed_at = now(), updated_at = now() WHERE id = %s",
                (new_password_hash, token["user_id"]),
            )
            await conn.execute(
                "UPDATE user_tokens SET used_at = now() "
                "WHERE user_id = %s AND token_type = 'password_reset' AND used_at IS NULL",
                (token["user_id"],),
            )
            return token["user_id"]


async def update_password(pool: AsyncConnectionPool, user_id: int, new_password_hash: str) -> None:
    """Saves the new password and sets password_changed_at = now() (one UPDATE statement)."""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE users SET password_hash = %s, password_changed_at = now(), updated_at = now() WHERE id = %s",
            (new_password_hash, user_id),
        )
