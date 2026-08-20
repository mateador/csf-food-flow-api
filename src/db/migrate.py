"""
Migration runner. Deliberately not Alembic -- this schema is small and
changes infrequently enough that a version-table + numbered-files pattern
is easier to reason about than a full migration framework.

Usage:
    python -m src.db.migrate           # applies any pending migrations
    python -m src.db.migrate --status  # shows applied vs. pending, no changes

Connects via DIRECT_URL (not DATABASE_URL) -- DDL statements like CREATE
TABLE can behave unreliably through Neon's transaction-mode pooler, so
migrations always use the direct, non-pooled connection.
"""
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def get_migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def get_applied_versions(conn: asyncpg.Connection) -> set[str]:
    await conn.execute(VERSION_TABLE_SQL)
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}


async def run():
    direct_url = os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not direct_url:
        print("ERROR: DIRECT_URL (or DATABASE_URL) not set", file=sys.stderr)
        sys.exit(1)

    status_only = "--status" in sys.argv

    conn = await asyncpg.connect(direct_url)
    try:
        applied = await get_applied_versions(conn)
        files = get_migration_files()

        if not files:
            print("No migration files found in", MIGRATIONS_DIR)
            return

        pending = [f for f in files if f.stem not in applied]

        if status_only:
            for f in files:
                marker = "applied" if f.stem in applied else "PENDING"
                print(f"  [{marker}] {f.stem}")
            return

        if not pending:
            print("Database is up to date. No pending migrations.")
            return

        for f in pending:
            print(f"Applying {f.stem}...")
            sql = f.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", f.stem
                )
            print(f"  -> applied {f.stem}")

        print(f"Done. Applied {len(pending)} migration(s).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
