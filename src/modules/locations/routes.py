from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import get_current_user, require_auth, require_role

locations_bp = Blueprint("locations", url_prefix="/locations")


def _serialize_location(row) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "type": row["type"], "active": row["active"]}


@locations_bp.get("/")
@require_auth
async def list_locations(request):
    """HUB users receive only their own assigned location, never the full list."""
    user = get_current_user(request)
    args = request.args

    async with pool().acquire() as conn:
        if user["role"] == "HUB":
            rows = await conn.fetch(
                "SELECT * FROM locations WHERE id = $1", user["location_id"]
            )
        else:
            conditions = ["1=1"]
            params = []
            if args.get("active") is not None:
                params.append(args.get("active").lower() == "true")
                conditions.append(f"active = ${len(params)}")
            if args.get("type"):
                params.append(args.get("type"))
                conditions.append(f"type = ${len(params)}")
            rows = await conn.fetch(
                f"SELECT * FROM locations WHERE {' AND '.join(conditions)} ORDER BY name", *params
            )

    return json_response([_serialize_location(r) for r in rows])


@locations_bp.post("/")
@require_auth
@require_role("ADMIN")
async def create_location(request):
    body = request.json or {}
    name, loc_type = body.get("name"), body.get("type")
    if not name or loc_type not in ("HUB", "FOOD_CENTRE"):
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "name and valid type are required"}},
            status=422,
        )
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO locations (name, type) VALUES ($1, $2) RETURNING *", name, loc_type
        )
    return json_response(_serialize_location(row), status=201)


@locations_bp.patch("/<location_id>")
@require_auth
@require_role("ADMIN")
async def update_location(request, location_id):
    body = request.json or {}
    fields, params = [], []
    for key in ("name", "type", "active"):
        if key in body:
            params.append(body[key])
            fields.append(f"{key} = ${len(params)}")
    if not fields:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "No fields to update"}}, status=422
        )
    params.append(location_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE locations SET {', '.join(fields)}, updated_at = now() "
            f"WHERE id = ${len(params)} RETURNING *",
            *params,
        )
    if not row:
        return json_response({"error": {"code": "NOT_FOUND", "message": "Location not found"}}, status=404)
    return json_response(_serialize_location(row))
