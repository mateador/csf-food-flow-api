import os
import re

from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import SESSION_COOKIE_NAME, require_auth
from src.modules.auth.email import send_login_code_email
from src.modules.auth.service import (
    can_request_new_code,
    change_own_pin,
    create_login_code,
    issue_session_jwt,
    pin_needs_setup,
    set_initial_pin,
    verify_and_consume_login_code,
    verify_pin,
)

auth_bp = Blueprint("auth", url_prefix="/auth")

IS_PRODUCTION = os.environ.get("SANIC_DEV", "false").lower() != "true"

PIN_PATTERN = re.compile(r"^\d{4}$")


def _serialize_user(user: dict) -> dict:
    return {
        "id": str(user["id"]),
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "location_id": str(user["location_id"]) if user["location_id"] else None,
        "active": user["active"],
        "last_login_at": user["last_login_at"].isoformat() if user["last_login_at"] else None,
    }


def _sign_in_response(user: dict):
    """Builds the {user: ...} body and sets the session cookie. Shared by
    every endpoint that ends in a successful sign-in: emailed-code verify,
    and both PIN endpoints below."""
    session_jwt = issue_session_jwt(user)
    response = json_response({"user": _serialize_user(user)})
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

    return _sign_in_response(result)


# --- PIN login ---------------------------------------------------------
#
# Replaces the emailed one-time code above as the PWA's default sign-in
# flow (see migration 0010 and service.py's PIN section for the full
# reasoning). The emailed-code endpoints above are untouched and still
# fully reachable -- deliberately kept dormant rather than removed.


@auth_bp.post("/pin/check")
async def check_pin_status(request):
    """Tells the frontend which form to show: first-time PIN setup, or a
    normal PIN entry. Unlike /code/request, this does reveal a little
    about account state (whether an active account exists and hasn't set
    a PIN yet) -- an accepted, deliberate trade-off for this flow, not an
    oversight."""
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "email is required"}}, status=422
        )
    return json_response({"needs_setup": await pin_needs_setup(email)})


@auth_bp.post("/pin/set")
async def set_pin(request):
    """First-time PIN setup only. Succeeds only for an active user who
    doesn't have a PIN yet -- changing an existing one goes through
    PATCH /auth/pin (self-service) or PATCH /users/<id>/pin (admin
    reset), never this endpoint."""
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()
    pin = (body.get("pin") or "").strip()
    pin_confirm = (body.get("pin_confirm") or "").strip()

    if not email or not PIN_PATTERN.match(pin):
        return json_response(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "email and a 4-digit pin are required",
                }
            },
            status=422,
        )
    if pin != pin_confirm:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "pin and pin_confirm must match"}},
            status=422,
        )

    user = await set_initial_pin(email, pin)
    if not user:
        return json_response(
            {
                "error": {
                    "code": "PIN_ALREADY_SET",
                    "message": "This account already has a PIN, or doesn't exist.",
                }
            },
            status=409,
        )
    return _sign_in_response(user)


@auth_bp.post("/pin/login")
async def login_with_pin(request):
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()
    pin = (body.get("pin") or "").strip()

    if not email or not pin:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "email and pin are required"}},
            status=422,
        )

    result = await verify_pin(email, pin)
    if not isinstance(result, dict):
        return json_response(
            {"error": {"code": "INVALID_PIN", "message": "Incorrect email or PIN."}},
            status=401,
        )
    return _sign_in_response(result)


@auth_bp.patch("/pin")
@require_auth
async def change_pin(request):
    """Self-service PIN change. Requires the CURRENT pin, not just a valid
    session -- this app runs on shared tablets that can stay signed in
    between volunteers, so proving the caller already knows the existing
    PIN is what stops one volunteer silently locking another out."""
    body = request.json or {}
    current_pin = (body.get("current_pin") or "").strip()
    pin = (body.get("pin") or "").strip()
    pin_confirm = (body.get("pin_confirm") or "").strip()

    if not PIN_PATTERN.match(pin):
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "pin must be 4 digits"}}, status=422
        )
    if pin != pin_confirm:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "pin and pin_confirm must match"}},
            status=422,
        )

    ok = await change_own_pin(request.ctx.user["sub"], current_pin, pin)
    if not ok:
        return json_response(
            {"error": {"code": "INVALID_PIN", "message": "Current PIN is incorrect."}}, status=401
        )
    return json_response({"status": "changed"})


@auth_bp.post("/logout")
async def logout(request):
    """
    Ends the session on THIS device by expiring the httpOnly cookie. The
    browser can't remove an httpOnly cookie itself, so without this call
    "Sign out" in the PWA would only forget the user locally while the
    cookie stayed valid -- and on a shared hub tablet, the next volunteer
    would be recording as the previous one.

    No auth required and always succeeds: signing out when already signed
    out is not an error. The attributes must match the ones the cookie was
    set with, or the browser treats it as a different cookie and keeps the
    original.
    """
    response = json_response({"status": "signed_out"})
    response.cookies.add_cookie(
        SESSION_COOKIE_NAME,
        "",
        path="/",
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="Lax",
        max_age=0,
    )
    return response