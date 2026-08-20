from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import get_current_user, require_auth

me_bp = Blueprint("me")


@me_bp.get("/me")
@require_auth
async def get_me(request):
    session = get_current_user(request)
    async with pool().acquire() as conn:
        user_row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", session["sub"])
    if not user_row:
        return json_response(
            {"error": {"code": "NOT_FOUND", "message": "User not found"}}, status=404
        )

    return json_response(
        {
            "id": str(user_row["id"]),
            "name": user_row["name"],
            "email": user_row["email"],
            "role": user_row["role"],
            "location_id": str(user_row["location_id"]) if user_row["location_id"] else None,
            "active": user_row["active"],
            "last_login_at": user_row["last_login_at"].isoformat()
            if user_row["last_login_at"]
            else None,
        }
    )
