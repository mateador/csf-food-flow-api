import os

from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import SESSION_COOKIE_NAME
from src.modules.auth.email import send_login_code_email
from src.modules.auth.service import (
    can_request_new_code,
    create_login_code,
    issue_session_jwt,
    verify_and_consume_login_code,
)

auth_bp = Blueprint("auth", url_prefix="/auth")

IS_PRODUCTION = os.environ.get("SANIC_DEV", "false").lower() != "true"


@auth_bp.post("/code/request")
async def request_login_code(request):
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()

    # Always return the same generic response whether or not the email
    # exists, is active, or is currently rate-limited -- prevents user
    # enumeration via response timing or content differences.
    if email:
        async with pool().acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT id FROM users WHERE email = $1 AND active = true", email
            )
        if user_row and await can_request_new_code(str(user_row["id"])):
            raw_code = await create_login_code(str(user_row["id"]), email)
            await send_login_code_email(email, raw_code)

    return json_response({"status": "requested"})


@auth_bp.post("/code/verify")
async def verify_login_code(request):
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()
    code = (body.get("code") or "").strip()

    if not email or not code:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "email and code are required"}},
            status=422,
        )

    result = await verify_and_consume_login_code(email, code)

    if result == "TOO_MANY_ATTEMPTS":
        return json_response(
            {
                "error": {
                    "code": "TOO_MANY_ATTEMPTS",
                    "message": "Too many incorrect attempts. Request a new code.",
                }
            },
            status=401,
        )
    if result == "EXPIRED":
        return json_response(
            {"error": {"code": "EXPIRED", "message": "This code has expired \u2014 request a new one."}},
            status=401,
        )
    if not isinstance(result, dict):
        return json_response(
            {"error": {"code": "INVALID_CODE", "message": "Incorrect code."}},
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
        samesite="Lax",
        max_age=int(os.environ.get("ACCESS_TOKEN_TTL_MINUTES", "720")) * 60,
    )
    return response