"""Database pool (Layer 7, item 2).

A pool of reusable async psycopg 3 connections, created at startup and closed at shutdown.
Repositories borrow a connection per call or per transaction.
"""

import logging

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings

logger = logging.getLogger("app.db")


def create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        min_size=settings.db_pool_min_size,
        max_size=max(settings.db_pool_max_size, settings.db_pool_min_size),
        timeout=settings.db_pool_timeout_seconds,
        kwargs={"row_factory": dict_row},
        open=False,
        name="sangra",
    )


async def open_pool(pool: AsyncConnectionPool) -> None:
    # Do not block startup on the database: if it is down, /health answers 503 until it is back.
    await pool.open(wait=False)


async def close_pool(pool: AsyncConnectionPool) -> None:
    await pool.close()
