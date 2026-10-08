from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import require_auth

out_destinations_bp = Blueprint("out_destinations", url_prefix="/out-destinations")


@out_destinations_bp.get("/")
@require_auth
async def list_out_destinations(request):
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM out_destinations WHERE active = true ORDER BY name"
        )
    return json_response(
        [{"id": str(r["id"]), "name": r["name"], "active": r["active"]} for r in rows]
    )
