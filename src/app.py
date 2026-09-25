"""
Cambridge Sustainable Food App -- API entrypoint.

Step 1 scope only: app boots, health check responds, CORS is configured
correctly for the httpOnly-cookie auth model. Route modules (auth, entries,
locations, etc.) land in Step 4 and get registered as blueprints here.
"""
import os

from dotenv import load_dotenv

load_dotenv()

import structlog
from sanic import Sanic
from sanic.response import json as json_response
from sanic_ext import Extend

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

app = Sanic("CSFFoodFlowAPI")

CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "http://localhost:5173")

# Cookie-based auth requires an exact origin -- CORS forbids sending
# credentials (cookies) to/from a wildcard "*" origin, so CORS_ORIGIN must
# always be the PWA's real origin, never "*". Using sanic-ext's native CORS
# handling (rather than hand-rolled middleware) so OPTIONS preflight
# handling and header injection come from one place, not two competing
# implementations.
app.config.CORS_ORIGINS = CORS_ORIGIN
app.config.CORS_SUPPORTS_CREDENTIALS = True
app.config.CORS_METHODS = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
app.config.CORS_ALLOW_HEADERS = ["content-type"]

Extend(app)

from src.db import client as db_client
from src.modules.auth.routes import auth_bp
from src.modules.auth.me_routes import me_bp
from src.modules.entries.routes import entries_bp
from src.modules.locations.routes import locations_bp
from src.modules.categories.routes import categories_bp
from src.modules.reports.routes import reports_bp
from src.modules.users.routes import users_bp
from src.modules.tray_types.routes import tray_types_bp

app.blueprint(auth_bp, url_prefix="/api/v1/auth")
app.blueprint(me_bp, url_prefix="/api/v1")
app.blueprint(entries_bp, url_prefix="/api/v1/entries")
app.blueprint(locations_bp, url_prefix="/api/v1/locations")
app.blueprint(categories_bp, url_prefix="/api/v1/categories")
app.blueprint(reports_bp, url_prefix="/api/v1/reports")
app.blueprint(users_bp, url_prefix="/api/v1/users")
app.blueprint(tray_types_bp, url_prefix="/api/v1/tray-types")


@app.before_server_start
async def setup_db(app):
    await db_client.init_pool()


@app.after_server_stop
async def teardown_db(app):
    await db_client.close_pool()


@app.get("/api/v1/health")
async def health(request):
    """Liveness: the process is up and answering. Deliberately touches
    nothing else, so it stays cheap and never wakes the database."""
    return json_response({"status": "ok", "version": "0.1.0"})


@app.get("/api/v1/health/ready")
async def readiness(request):
    """
    Readiness: the API can actually serve users, which means it can reach
    the database. Used by the uptime check and the deploy check -- a wrong
    DATABASE_URL secret, a revoked role or a Neon outage leaves /health
    saying "ok" while every sign-in fails, and this catches that.

    Wakes Neon's compute if it has scaled to zero, so it's only called on
    a schedule, never by the PWA.
    """
    try:
        async with db_client.pool().acquire(timeout=10) as conn:
            await conn.fetchval("SELECT 1", timeout=10)
    except Exception:
        logger.error("readiness_database_unreachable", exc_info=True)
        return json_response({"status": "unavailable", "database": "unreachable"}, status=503)
    return json_response({"status": "ok", "database": "ok"})


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        dev=os.environ.get("SANIC_DEV", "false").lower() == "true",
    )
