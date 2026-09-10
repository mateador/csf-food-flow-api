from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import require_auth

tray_types_bp = Blueprint("tray_types", url_prefix="/tray-types")


@tray_types_bp.get("/")
@require_auth
async def list_tray_types(request):
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM tray_types WHERE active = true ORDER BY code")
    return json_response(
        [
            {"code": r["code"], "name": r["name"], "weight_kg": float(r["weight_kg"]), "active": r["active"]}
            for r in rows
        ]
    )
