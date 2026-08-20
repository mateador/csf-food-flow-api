from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import require_auth

categories_bp = Blueprint("categories", url_prefix="/categories")


@categories_bp.get("/")
@require_auth
async def list_categories(request):
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM food_categories WHERE active = true ORDER BY code")
    return json_response(
        [{"code": r["code"], "name": r["name"], "active": r["active"]} for r in rows]
    )
