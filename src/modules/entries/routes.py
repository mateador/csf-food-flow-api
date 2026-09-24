from datetime import date

from sanic import Blueprint
from sanic.response import json as json_response

from src.db.client import pool
from src.middleware.auth import require_auth

entries_bp = Blueprint("entries", url_prefix="/entries")


def _serialize_entry(row, trays: list | None = None) -> dict:
    return {
        "id": str(row["id"]),
        "client_uuid": str(row["client_uuid"]) if row["client_uuid"] else None,
        "entry_type": row["entry_type"],
        "location_id": str(row["location_id"]),
        "destination_location_id": str(row["destination_location_id"])
        if row["destination_location_id"]
        else None,
        "name": row["name"],
        "food_category_code": row["food_category_code"],
        "gross_weight_kg": float(row["gross_weight_kg"]),
        "net_weight_kg": float(row["net_weight_kg"]),
        "trays": trays if trays is not None else [],
        "collection_date": row["collection_date"].isoformat(),
        "notes": row["notes"],
        "status": row["status"],
        "void_reason": row["void_reason"],
        "created_by": str(row["created_by"]),
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


async def _validate_and_price_trays(conn, trays_input: list) -> tuple[list, float]:
    """
    trays_input: [{"tray_type_code": str, "quantity": int}, ...]
    Returns (validated_trays_with_weight, total_tray_weight_kg).
    Raises ValueError (caller turns this into a 422) on any problem --
    unknown/inactive code, non-positive quantity, or the same tray type
    listed twice (should be one line with a combined quantity instead).
    """
    if not trays_input:
        return [], 0.0

    tray_type_rows = await conn.fetch("SELECT code, weight_kg FROM tray_types WHERE active = true")
    tray_weights = {r["code"]: float(r["weight_kg"]) for r in tray_type_rows}

    seen_codes = set()
    validated = []
    total_tray_weight = 0.0

    for item in trays_input:
        code = item.get("tray_type_code")
        quantity = item.get("quantity")

        if code in seen_codes:
            raise ValueError(f"Duplicate tray_type_code in trays: {code}")
        seen_codes.add(code)

        if code not in tray_weights:
            raise ValueError(f"Unknown or inactive tray_type_code: {code}")
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError(f"quantity for tray {code} must be a positive integer")

        tray_weight = tray_weights[code]
        total_tray_weight += tray_weight * quantity
        validated.append({"tray_type_code": code, "quantity": quantity, "weight_kg": tray_weight})

    return validated, total_tray_weight


async def _fetch_trays_for_entries(conn, entry_ids: list[str]) -> dict[str, list]:
    """
    Batched tray lookup for a set of entries -- one query for however many
    entries are being returned, not one query per entry. Returns
    {entry_id: [tray, ...]}; entries with no trays simply don't appear as
    a key (callers should use .get(id, [])).
    """
    if not entry_ids:
        return {}
    rows = await conn.fetch(
        """SELECT et.entry_id, et.tray_type_code, et.quantity, tt.weight_kg, tt.name
           FROM entry_trays et
           JOIN tray_types tt ON tt.code = et.tray_type_code
           WHERE et.entry_id = ANY($1::uuid[])""",
        entry_ids,
    )
    by_entry: dict[str, list] = {}
    for r in rows:
        by_entry.setdefault(str(r["entry_id"]), []).append(
            {
                "tray_type_code": r["tray_type_code"],
                "tray_type_name": r["name"],
                "quantity": r["quantity"],
                "weight_kg": float(r["weight_kg"]),
            }
        )
    return by_entry


@entries_bp.post("/")
@require_auth
async def create_entry(request):
    """
    Role rules (enforced HERE, server-side -- never trust the client):
      HUB: may only create IN entries, only at their own assigned location,
           destination_location_id must be null.
      FOOD_CENTRE: may create IN at any location, or OUT with a destination.
      ADMIN: may create either, for any valid location.

    net_weight_kg is ALWAYS computed here, server-side, from the tray
    selection -- a client-submitted net figure is never trusted, the same
    principle as every other calculation in this API.
    """
    user = request.ctx.user
    body = request.json or {}

    entry_type = body.get("entry_type")
    location_id = body.get("location_id")
    destination_location_id = body.get("destination_location_id")
    name = body.get("name")
    food_category_code = body.get("food_category_code")
    gross_weight_kg = body.get("gross_weight_kg")
    collection_date = body.get("collection_date")
    notes = body.get("notes")
    client_uuid = body.get("client_uuid")
    trays_input = body.get("trays", [])

    if not all([entry_type, location_id, name, food_category_code, gross_weight_kg, collection_date]):
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "Missing required field"}},
            status=422,
        )

    try:
        collection_date = date.fromisoformat(collection_date)
    except (ValueError, TypeError):
        return json_response(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "collection_date must be YYYY-MM-DD",
                }
            },
            status=422,
        )

    # --- Role enforcement: the actual security boundary ---
    if user["role"] == "HUB":
        if entry_type != "IN":
            return json_response(
                {"error": {"code": "FORBIDDEN", "message": "HUB users may only create IN entries"}},
                status=403,
            )
        if location_id != user["location_id"]:
            return json_response(
                {
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "HUB users may only create entries at their own location",
                    }
                },
                status=403,
            )
        if destination_location_id is not None:
            return json_response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "IN entries must not have a destination",
                    }
                },
                status=422,
            )
    elif user["role"] == "FOOD_CENTRE":
        if entry_type == "OUT" and not destination_location_id:
            return json_response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "OUT entries require a destination_location_id",
                    }
                },
                status=422,
            )
    # ADMIN: no additional restriction beyond the DB-level CHECK constraints.

    async with pool().acquire() as conn:
        try:
            validated_trays, total_tray_weight = await _validate_and_price_trays(conn, trays_input)
        except ValueError as exc:
            return json_response({"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}, status=422)

        net_weight_kg = round(float(gross_weight_kg) - total_tray_weight, 2)
        if net_weight_kg < 0:
            return json_response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Selected trays weigh more than the gross weight entered -- "
                        "check the weight and tray selection",
                    }
                },
                status=422,
            )

        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """INSERT INTO weigh_entries
                       (client_uuid, entry_type, location_id, destination_location_id,
                        name, food_category_code, gross_weight_kg, net_weight_kg,
                        collection_date, notes, created_by)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                       RETURNING *""",
                    client_uuid,
                    entry_type,
                    location_id,
                    destination_location_id,
                    name,
                    food_category_code,
                    gross_weight_kg,
                    net_weight_kg,
                    collection_date,
                    notes,
                    user["sub"],
                )

                for tray in validated_trays:
                    await conn.execute(
                        """INSERT INTO entry_trays (entry_id, tray_type_code, quantity)
                           VALUES ($1, $2, $3)""",
                        row["id"],
                        tray["tray_type_code"],
                        tray["quantity"],
                    )
        except Exception as exc:
            # DB CHECK constraints are the final backstop -- if application
            # logic above ever has a gap, the database still refuses to
            # store an invalid row. The transaction rolls back entirely
            # (entry + trays together) if anything here fails.
            return json_response(
                {"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}, status=422
            )

    return json_response({"entry": _serialize_entry(row, validated_trays)}, status=201)


@entries_bp.get("/")
@require_auth
async def list_entries(request):
    """
    HUB is restricted to their own location_id server-side -- any
    location_id query param they pass is ignored, never trusted.
    """
    user = request.ctx.user
    args = request.args

    conditions = ["1=1"]
    params = []

    def add_param(value):
        params.append(value)
        return f"${len(params)}"

    if user["role"] == "HUB":
        conditions.append(f"location_id = {add_param(user['location_id'])}")
    elif args.get("location_id"):
        conditions.append(f"location_id = {add_param(args.get('location_id'))}")

    if args.get("status"):
        conditions.append(f"status = {add_param(args.get('status'))}")
    else:
        conditions.append("status = 'ACTIVE'")

    if args.get("entry_type"):
        conditions.append(f"entry_type = {add_param(args.get('entry_type'))}")

    if args.get("food_category_code"):
        conditions.append(f"food_category_code = {add_param(args.get('food_category_code'))}")

    if args.get("week_start"):
        from datetime import timedelta

        week_start = date.fromisoformat(args.get("week_start"))
        week_end = week_start + timedelta(days=6)
        conditions.append(f"collection_date >= {add_param(week_start)}")
        conditions.append(f"collection_date <= {add_param(week_end)}")
    else:
        if args.get("from"):
            conditions.append(f"collection_date >= {add_param(date.fromisoformat(args.get('from')))}")
        if args.get("to"):
            conditions.append(f"collection_date <= {add_param(date.fromisoformat(args.get('to')))}")

    page = int(args.get("page", 1))
    limit = min(int(args.get("limit", 50)), 100)
    offset = (page - 1) * limit

    where_clause = " AND ".join(conditions)

    async with pool().acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM weigh_entries WHERE {where_clause}", *params
        )
        limit_param = add_param(limit)
        offset_param = add_param(offset)
        rows = await conn.fetch(
            f"""SELECT * FROM weigh_entries WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT {limit_param} OFFSET {offset_param}""",
            *params,
        )

        entry_ids = [str(r["id"]) for r in rows]
        trays_by_entry = await _fetch_trays_for_entries(conn, entry_ids)

    return json_response(
        {
            "entries": [_serialize_entry(r, trays_by_entry.get(str(r["id"]), [])) for r in rows],
            "pagination": {"page": page, "limit": limit, "total": total},
        }
    )


@entries_bp.post("/bulk")
@require_auth
async def bulk_sync_entries(request):
    """
    Offline-sync endpoint. Idempotent via client_uuid: an entry that
    already exists is reported as 'duplicate', not an error -- this is the
    behaviour the PWA's offline queue depends on when retrying a sync that
    partially succeeded before a connection drop.
    """
    user = request.ctx.user
    body = request.json or {}
    entries = body.get("entries", [])

    results = []
    async with pool().acquire() as conn:
        for item in entries:
            client_uuid = item.get("client_uuid")

            existing = await conn.fetchrow(
                "SELECT id FROM weigh_entries WHERE client_uuid = $1", client_uuid
            )
            if existing:
                results.append(
                    {"client_uuid": client_uuid, "status": "duplicate", "id": str(existing["id"])}
                )
                continue

            # Same role rules as single-entry create.
            if user["role"] == "HUB" and (
                item.get("entry_type") != "IN" or item.get("location_id") != user["location_id"]
            ):
                results.append(
                    {
                        "client_uuid": client_uuid,
                        "status": "error",
                        "message": "Not permitted for this role/location",
                    }
                )
                continue

            try:
                validated_trays, total_tray_weight = await _validate_and_price_trays(
                    conn, item.get("trays", [])
                )
            except ValueError as exc:
                results.append({"client_uuid": client_uuid, "status": "error", "message": str(exc)})
                continue

            gross_weight_kg = item.get("gross_weight_kg")
            net_weight_kg = round(float(gross_weight_kg) - total_tray_weight, 2) if gross_weight_kg else None
            if net_weight_kg is not None and net_weight_kg < 0:
                results.append(
                    {
                        "client_uuid": client_uuid,
                        "status": "error",
                        "message": "Selected trays weigh more than the gross weight entered",
                    }
                )
                continue

            try:
                collection_date = date.fromisoformat(item.get("collection_date"))
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """INSERT INTO weigh_entries
                           (client_uuid, entry_type, location_id, destination_location_id,
                            name, food_category_code, gross_weight_kg, net_weight_kg,
                            collection_date, notes, created_by)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                           RETURNING id""",
                        client_uuid,
                        item.get("entry_type"),
                        item.get("location_id"),
                        item.get("destination_location_id"),
                        item.get("name"),
                        item.get("food_category_code"),
                        gross_weight_kg,
                        net_weight_kg,
                        collection_date,
                        item.get("notes"),
                        user["sub"],
                    )
                    for tray in validated_trays:
                        await conn.execute(
                            """INSERT INTO entry_trays (entry_id, tray_type_code, quantity)
                               VALUES ($1, $2, $3)""",
                            row["id"],
                            tray["tray_type_code"],
                            tray["quantity"],
                        )
                results.append(
                    {"client_uuid": client_uuid, "status": "created", "id": str(row["id"])}
                )
            except Exception as exc:
                results.append({"client_uuid": client_uuid, "status": "error", "message": str(exc)})

    return json_response({"results": results})