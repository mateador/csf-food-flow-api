import os

from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import SESSION_COOKIE_NAME, get_current_user, require_auth
from src.modules.auth.email import send_magic_link_email
from src.modules.auth.service import (
    create_magic_link_token,
    issue_session_jwt,
    verify_and_consume_magic_link_token,
)

auth_bp = Blueprint("auth", url_prefix="/auth")

MAGIC_LINK_BASE_URL = os.environ.get("MAGIC_LINK_BASE_URL", "http://localhost:5173")
IS_PRODUCTION = os.environ.get("SANIC_DEV", "false").lower() != "true"


@auth_bp.post("/magic-link/request")
async def request_magic_link(request):
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()

    # Always return the same generic response whether or not the email
    # exists or is active -- prevents user enumeration via response timing
    # or content differences.
    if email:
        async with pool().acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT id FROM users WHERE email = $1 AND active = true", email
            )
        if user_row:
            raw_token = await create_magic_link_token(str(user_row["id"]))
            magic_link_url = f"{MAGIC_LINK_BASE_URL}/auth/verify?token={raw_token}"
            await send_magic_link_email(email, magic_link_url)

    return json_response({"status": "requested"})


@auth_bp.post("/magic-link/verify")
async def verify_magic_link(request):
    body = request.json or {}
    token = body.get("token")
    if not token:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "token is required"}},
            status=422,
        )

    result = await verify_and_consume_magic_link_token(token)

    if result == "ALREADY_USED":
        return json_response(
            {"error": {"code": "ALREADY_USED", "message": "This link has already been used."}},
            status=401,
        )
    if result == "EXPIRED":
        return json_response(
            {"error": {"code": "EXPIRED", "message": "This link has expired \u2014 request a new one."}},
            status=401,
        )
    if not result:
        return json_response(
            {"error": {"code": "UNAUTHORIZED", "message": "Invalid or expired token"}},
            status=401,
        )

    user = result

    session_jwt = issue_session_jwt(user)

    response = json_response(
        {
            "user": {
                "id": str(user["id"]),
                "name": user["name"],
                "email": user["email"],
                "role": user["role"],
                "location_id": str(user["location_id"]) if user["location_id"] else None,
                "active": user["active"],
                "last_login_at": user["last_login_at"].isoformat()
                if user["last_login_at"]
                else None,
            }
        }
    )
    response.cookies.add_cookie(
        SESSION_COOKIE_NAME,
        session_jwt,
        path="/",
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="None" if IS_PRODUCTION else "Lax",
        max_age=int(os.environ.get("ACCESS_TOKEN_TTL_MINUTES", "720")) * 60,
    )
    return response
