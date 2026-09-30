from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import require_auth, require_role

source_locations_bp = Blueprint("source_locations", url_prefix="/source-locations")


def _serialize_source_location(row) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "active": row["active"]}


@source_locations_bp.get("/")
@require_auth
async def list_source_locations(request):
    args = request.args
    conditions = ["1=1"]
    params = []
    if args.get("active") is not None:
        params.append(args.get("active").lower() == "true")
        conditions.append(f"active = ${len(params)}")

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM source_locations WHERE {' AND '.join(conditions)} ORDER BY name",
            *params,
        )

    return json_response([_serialize_source_location(r) for r in rows])


@source_locations_bp.post("/")
@require_auth
@require_role("ADMIN")
async def create_source_location(request):
    body = request.json or {}
    name = body.get("name")
    if not name:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "name is required"}},
            status=422,
        )
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO source_locations (name) VALUES ($1) RETURNING *", name
        )
    return json_response(_serialize_source_location(row), status=201)


@source_locations_bp.patch("/<source_location_id>")
@require_auth
@require_role("ADMIN")
async def update_source_location(request, source_location_id):
    body = request.json or {}
    fields, params = [], []
    for key in ("name", "active"):
        if key in body:
            params.append(body[key])
            fields.append(f"{key} = ${len(params)}")
    if not fields:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "No fields to update"}}, status=422
        )
    params.append(source_location_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE source_locations SET {', '.join(fields)}, updated_at = now() "
            f"WHERE id = ${len(params)} RETURNING *",
            *params,
        )
    if not row:
        return json_response(
            {"error": {"code": "NOT_FOUND", "message": "Source location not found"}}, status=404
        )
    return json_response(_serialize_source_location(row))
