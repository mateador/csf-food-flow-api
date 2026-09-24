"""
Test harness for the CSF Food Flow API.

Every test runs against a REAL Postgres database, never a mock. The rules
this API exists to enforce (role boundaries, the CHECK constraints behind
them, client_uuid idempotency, login-code lockout) live partly in SQL, so
a mocked database would test the wrong thing.

Safety: the suite drops and recreates the `public` schema of the database
in TEST_DATABASE_URL. It refuses to start unless that variable is set and
the database name contains "test", so it can never be pointed at Neon's
production database by accident.
"""
import asyncio
import os
from datetime import date
from urllib.parse import urlparse

import pytest
from dotenv import load_dotenv

# --- Environment must be set BEFORE the app is imported --------------------
# Load .env first so TEST_DATABASE_URL can live there alongside everything
# else. Then override what the tests must control: the database the app
# connects to, the JWT secret, and email (never send real email from a
# test). service.py reads JWT_SECRET at import time, so this has to happen
# before `from src.app import app` below.
load_dotenv()
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.exit(
        "TEST_DATABASE_URL is not set. Point it at a disposable Postgres "
        "database -- see 'Running the tests' in the README.",
        returncode=2,
    )
_db_name = urlparse(TEST_DATABASE_URL).path.lstrip("/")
if "test" not in _db_name:
    pytest.exit(
        f"Refusing to run: database '{_db_name}' does not contain 'test' in "
        "its name. The suite drops the whole schema.",
        returncode=2,
    )

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET"] = "test-secret-not-used-anywhere-else"
os.environ["RESEND_API_KEY"] = ""  # never send real email from tests

import asyncpg  # noqa: E402

from src.app import app  # noqa: E402
from src.db.migrate import get_migration_files  # noqa: E402
from src.db.seed import CATEGORIES  # noqa: E402
from src.middleware.auth import SESSION_COOKIE_NAME  # noqa: E402
from src.modules.auth.service import issue_session_jwt  # noqa: E402

# A Monday, so it's a valid week_start for reports.
MONDAY = date(2026, 9, 21)


# --- Schema: built once per test run ---------------------------------------
async def _rebuild_schema():
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        # Apply the real migration files, in order -- the tests run against
        # exactly the schema production runs on.
        for migration in get_migration_files():
            await conn.execute(migration.read_text())
        for code, name in CATEGORIES:
            await conn.execute(
                "INSERT INTO food_categories (code, name) VALUES ($1, $2) "
                "ON CONFLICT (code) DO NOTHING",
                code,
                name,
            )
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def schema():
    asyncio.run(_rebuild_schema())


# --- Data: wiped before every test ------------------------------------------
@pytest.fixture
async def db():
    """A direct connection for arranging and inspecting data. Every test
    starts with no users, locations, entries or login codes, and with all
    reference data (categories, tray types) active."""
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    await conn.execute(
        "TRUNCATE entry_trays, weigh_entries, audit_log, login_codes, users, locations CASCADE"
    )
    await conn.execute("UPDATE food_categories SET active = true")
    await conn.execute("DELETE FROM food_categories WHERE code LIKE 'TEST_%'")
    await conn.execute("UPDATE tray_types SET active = true")
    yield conn
    await conn.close()


# --- HTTP client ------------------------------------------------------------
class Api:
    """Thin wrapper over Sanic's ASGI test client. Pass `user=` to send the
    request with a valid session cookie for that user; omit it to send the
    request signed out."""

    def __init__(self, sanic_app):
        self._client = sanic_app.asgi_client

    async def _send(self, method, path, *, user=None, headers=None, **kwargs):
        headers = dict(headers or {})
        if user is not None:
            headers["cookie"] = f"{SESSION_COOKIE_NAME}={issue_session_jwt(user)}"
        _, response = await getattr(self._client, method)(path, headers=headers, **kwargs)
        return response

    async def get(self, path, **kwargs):
        return await self._send("get", path, **kwargs)

    async def post(self, path, **kwargs):
        return await self._send("post", path, **kwargs)

    async def patch(self, path, **kwargs):
        return await self._send("patch", path, **kwargs)


@pytest.fixture
def api(db):
    # Depends on `db` so every request runs against a freshly wiped database.
    return Api(app)


# --- Factories --------------------------------------------------------------
@pytest.fixture
def make_location(db):
    async def _make(name, loc_type="HUB", active=True):
        row = await db.fetchrow(
            "INSERT INTO locations (name, type, active) VALUES ($1, $2, $3) RETURNING id",
            name,
            loc_type,
            active,
        )
        return str(row["id"])

    return _make


@pytest.fixture
def make_user(db):
    async def _make(role, location_id=None, email=None, name=None, active=True):
        email = email or f"{role.lower()}-{os.urandom(3).hex()}@example.org"
        row = await db.fetchrow(
            """INSERT INTO users (name, email, role, location_id, active)
               VALUES ($1, $2, $3, $4, $5) RETURNING *""",
            name or f"Test {role}",
            email,
            role,
            location_id,
            active,
        )
        return dict(row)

    return _make


@pytest.fixture
async def world(make_location, make_user):
    """The standard cast: one food centre, two hubs, and a user of each role.
    `hub_user` belongs to `hub`; nobody belongs to `other_hub`."""
    centre = await make_location("CSF Food Centre", "FOOD_CENTRE")
    hub = await make_location("North Hub", "HUB")
    other_hub = await make_location("South Hub", "HUB")
    return {
        "centre": centre,
        "hub": hub,
        "other_hub": other_hub,
        "admin": await make_user("ADMIN"),
        "centre_user": await make_user("FOOD_CENTRE", location_id=centre),
        "hub_user": await make_user("HUB", location_id=hub),
    }


def entry_payload(location_id, **overrides):
    """A valid IN entry at `location_id`. Override any field per test."""
    payload = {
        "entry_type": "IN",
        "location_id": location_id,
        "destination_location_id": None,
        "name": "Tesco Newmarket Road",
        "food_category_code": "FRESH",
        "gross_weight_kg": 10.0,
        "collection_date": MONDAY.isoformat(),
        "trays": [],
    }
    payload.update(overrides)
    return payload