"""
Connection pool management. One pool for the whole app lifetime, created on
Sanic's before_server_start and closed on after_server_stop (wired up in
src/app.py in Step 4).
"""
import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    database_url = os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
    return _pool


async def close_pool():
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            "DB pool not initialized -- init_pool() must run before any query "
            "(normally via Sanic's before_server_start listener)"
        )
    return _pool
