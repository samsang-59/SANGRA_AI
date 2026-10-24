"""Database pool (Layer 7, item 2).

A pool of reusable async psycopg 3 connections, created at startup and closed at shutdown.
Repositories borrow a connection per call or per transaction.
"""

import logging

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from psycopg_pool.base import AttemptWithBackoff

from app.core.config import Settings

logger = logging.getLogger("app.db")

# psycopg-pool retries a failed connection after 1, 2, 4, 8 ... seconds (doubling, for up to 5 minutes).
# After a database outage of one minute, the app would stay broken for up to another minute after the
# database is back (B3-R01, B11-R01). A steady retry about once a second (with a little jitter) is cheap
# and brings the app back right away. psycopg-pool is pinned; a test checks this setting still exists.
AttemptWithBackoff.DELAY_BACKOFF = 1.0


def create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        min_size=min(settings.db_pool_min_size, settings.db_pool_max_size),
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout_seconds,
        kwargs={"row_factory": dict_row},
        # A connection is checked before it is handed out, so a connection broken while it sat idle
        # (database restart, killed backend) is replaced instead of failing a request.
        check=AsyncConnectionPool.check_connection,
        open=False,
        name="sangra",
    )


async def open_pool(pool: AsyncConnectionPool) -> None:
    # Do not block startup on the database: if it is down, /health answers 503 until it is back.
    await pool.open(wait=False)


async def close_pool(pool: AsyncConnectionPool) -> None:
    await pool.close()
