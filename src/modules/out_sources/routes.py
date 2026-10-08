from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import require_auth

out_sources_bp = Blueprint("out_sources", url_prefix="/out-sources")


@out_sources_bp.get("/")
@require_auth
async def list_out_sources(request):
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM out_sources WHERE active = true ORDER BY name")
    return json_response(
        [{"id": str(r["id"]), "name": r["name"], "active": r["active"]} for r in rows]
    )
