"""PostgreSQL connection-pool lifecycle only — no schema, tables, or migrations."""

from __future__ import annotations

import asyncpg
from asyncpg import Pool

from app.config.runtime import Settings
from app.core.logging import get_logger

logger = get_logger("database")

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5
CONNECTION_TIMEOUT_SECONDS = 10.0
COMMAND_TIMEOUT_SECONDS = 10.0


async def connect(settings: Settings) -> Pool | None:
    """Open and validate the configured pool. Missing DATABASE_URL disables it silently; a bad DSN raises."""
    if settings.DATABASE_URL is None:
        logger.info("Database connection disabled: DATABASE_URL is not configured")
        return None

    dsn = settings.DATABASE_URL.get_secret_value().strip()
    if not dsn:
        logger.info("Database connection disabled: DATABASE_URL is empty")
        return None

    pool: Pool | None = None
    try:
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            timeout=CONNECTION_TIMEOUT_SECONDS,
            command_timeout=COMMAND_TIMEOUT_SECONDS,
        )
        async with pool.acquire() as connection:
            await connection.execute("SELECT 1")
    except Exception:
        if pool is not None:
            await pool.close()
        logger.exception("Database connection failed")
        raise

    logger.info("Database connection established")
    return pool


async def disconnect(pool: Pool | None) -> None:
    """Close a pool created by :func:`connect`."""
    if pool is None:
        return

    await pool.close()
    logger.info("Database connection closed")
