"""
Idempotent seed script. Safe to run multiple times -- uses ON CONFLICT DO
NOTHING throughout, so re-running never duplicates or errors on existing
data.

Usage:
    python -m src.db.seed
"""
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv()

CATEGORIES = [
    ("FRESH", "Fresh"),
    ("FROZEN", "Frozen"),
    ("AMBIENT", "Ambient"),
]

# One food centre + one hub, enough to exercise IN and OUT entries locally
# without needing to create locations through the admin UI first.
LOCATIONS = [
    ("CSF Food Centre", "FOOD_CENTRE"),
    ("Example Hub", "HUB"),
]

# The initial admin account. No password -- auth is magic-link only, so
# this user signs in the same way anyone else does: request a link at
# /login, no separate bootstrap credential needed.
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "alexandre.als@gmail.com")
ADMIN_NAME = "CSF Admin"


async def run():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        async with conn.transaction():
            print("Seeding food categories...")
            for code, name in CATEGORIES:
                await conn.execute(
                    """INSERT INTO food_categories (code, name)
                       VALUES ($1, $2)
                       ON CONFLICT (code) DO NOTHING""",
                    code,
                    name,
                )

            print("Seeding locations...")
            location_ids = {}
            for name, loc_type in LOCATIONS:
                row = await conn.fetchrow(
                    """INSERT INTO locations (name, type)
                       VALUES ($1, $2)
                       ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                       RETURNING id, type""",
                    name,
                    loc_type,
                )
                location_ids[loc_type] = row["id"]

            print("Seeding admin user...")
            await conn.execute(
                """INSERT INTO users (name, email, role, location_id)
                   VALUES ($1, $2, 'ADMIN', NULL)
                   ON CONFLICT (email) DO NOTHING""",
                ADMIN_NAME,
                ADMIN_EMAIL.lower(),
            )

        print("Seed complete.")
        print(f"  Admin login email: {ADMIN_EMAIL}")
        print(f"  Locations: {LOCATIONS}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
