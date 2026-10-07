import re

from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import require_auth, require_role
from src.modules.auth.service import admin_reset_pin

users_bp = Blueprint("users", url_prefix="/users")

PIN_PATTERN = re.compile(r"^\d{4}$")


def _serialize_user(row) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "location_id": str(row["location_id"]) if row["location_id"] else None,
        "active": row["active"],
        "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
    }


@users_bp.get("/")
@require_auth
@require_role("ADMIN")
async def list_users(request):
    args = request.args
    conditions, params = ["1=1"], []
    if args.get("active") is not None:
        params.append(args.get("active").lower() == "true")
        conditions.append(f"active = ${len(params)}")
    if args.get("role"):
        params.append(args.get("role"))
        conditions.append(f"role = ${len(params)}")

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM users WHERE {' AND '.join(conditions)} ORDER BY name", *params
        )
    return json_response([_serialize_user(r) for r in rows])


@users_bp.post("/")
@require_auth
@require_role("ADMIN")
async def create_user(request):
    body = request.json or {}
    name, email, role = body.get("name"), body.get("email"), body.get("role")
    location_id = body.get("location_id")

    if not name or not email or role not in ("ADMIN", "FOOD_CENTRE", "HUB"):
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "name, email, and valid role are required"}},
            status=422,
        )
    if role == "HUB" and not location_id:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "location_id is required for HUB role"}},
            status=422,
        )

    async with pool().acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO users (name, email, role, location_id)
                   VALUES ($1, $2, $3, $4) RETURNING *""",
                name,
                email.lower(),
                role,
                location_id,
            )
        except Exception as exc:
            return json_response(
                {"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}, status=422
            )
    return json_response(_serialize_user(row), status=201)


@users_bp.patch("/<user_id>")
@require_auth
@require_role("ADMIN")
async def update_user(request, user_id):
    body = request.json or {}
    fields, params = [], []
    for key in ("name", "email", "role", "location_id", "active"):
        if key in body:
            value = body[key].lower() if key == "email" and body[key] else body[key]
            params.append(value)
            fields.append(f"{key} = ${len(params)}")
    if not fields:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "No fields to update"}}, status=422
        )
    params.append(user_id)

    async with pool().acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"UPDATE users SET {', '.join(fields)}, updated_at = now() "
                f"WHERE id = ${len(params)} RETURNING *",
                *params,
            )
        except Exception as exc:
            return json_response(
                {"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}, status=422
            )
    if not row:
        return json_response({"error": {"code": "NOT_FOUND", "message": "User not found"}}, status=404)
    return json_response(_serialize_user(row))


@users_bp.patch("/<user_id>/pin")
@require_auth
@require_role("ADMIN")
async def reset_user_pin(request, user_id):
    """Recovery path for a forgotten PIN -- deliberately no current-pin
    check (see service.admin_reset_pin), since the whole point is that the
    admin doesn't know the old one. Any admin can reset any user's PIN,
    including another admin's, so this is logged to audit_log."""
    body = request.json or {}
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

    user = await admin_reset_pin(user_id, pin)
    if not user:
        return json_response({"error": {"code": "NOT_FOUND", "message": "User not found"}}, status=404)

    async with pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_log (entity_type, entity_id, user_id, action)
               VALUES ('USER', $1, $2, 'UPDATE')""",
            user_id,
            request.ctx.user["sub"],
        )

    return json_response(_serialize_user(user))
