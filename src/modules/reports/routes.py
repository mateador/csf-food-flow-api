import csv
import io
from datetime import date, timedelta

from sanic import Blueprint
from sanic.response import json as json_response, text as text_response

from src.db.client import pool
from src.middleware.auth import get_current_user, require_auth, require_role

reports_bp = Blueprint("reports", url_prefix="/reports")

CATEGORY_CODES = ["FRESH", "FROZEN", "AMBIENT"]


def _empty_category_totals() -> dict:
    return {code: 0.0 for code in CATEGORY_CODES}


async def _compute_weekly_totals(week_start: date, location_filter: str | None):
    """
    Shared logic between the JSON report and the CSV export -- both need
    the exact same Monday-to-Sunday, category-and-location-grouped totals,
    so this is written once and consumed by both routes.
    """
    week_end = week_start + timedelta(days=6)

    conditions = ["status = 'ACTIVE'", "collection_date >= $1", "collection_date <= $2"]
    params: list = [week_start, week_end]

    if location_filter:
        params.append(location_filter)
        conditions.append(f"location_id = ${len(params)}")

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT location_id, destination_location_id, entry_type,
                       food_category_code, weight_kg
                FROM weigh_entries
                WHERE {' AND '.join(conditions)}""",
            *params,
        )
        location_rows = await conn.fetch("SELECT id, name FROM locations")

    location_names = {str(r["id"]): r["name"] for r in location_rows}

    totals_in = _empty_category_totals()
    totals_out = _empty_category_totals()
    by_location: dict[str, dict] = {}

    def ensure_location(loc_id: str) -> dict:
        if loc_id not in by_location:
            by_location[loc_id] = {
                "location_id": loc_id,
                "location_name": location_names.get(loc_id, "Unknown"),
                "in_by_category": _empty_category_totals(),
                "out_by_category": _empty_category_totals(),
            }
        return by_location[loc_id]

    for row in rows:
        weight = float(row["weight_kg"])
        code = row["food_category_code"]
        if row["entry_type"] == "IN":
            totals_in[code] += weight
            ensure_location(str(row["location_id"]))["in_by_category"][code] += weight
        else:  # OUT
            totals_out[code] += weight
            ensure_location(str(row["location_id"]))["out_by_category"][code] += weight

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "totals": {"in_by_category": totals_in, "out_by_category": totals_out},
        "by_location": list(by_location.values()),
    }


def _validate_week_start(raw: str) -> tuple[date | None, str | None]:
    try:
        parsed = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None, "week_start must be a valid YYYY-MM-DD date"
    if parsed.weekday() != 0:  # Monday == 0
        return None, "week_start must be a Monday"
    return parsed, None


@reports_bp.get("/weekly")
@require_auth
async def weekly_report(request):
    user = get_current_user(request)
    raw_week_start = request.args.get("week_start")
    if not raw_week_start:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "week_start is required"}}, status=422
        )

    week_start, error = _validate_week_start(raw_week_start)
    if error:
        return json_response({"error": {"code": "VALIDATION_ERROR", "message": error}}, status=422)

    location_filter = request.args.get("location_id")
    if user["role"] == "HUB":
        # HUB may only see their own hub -- any requested location_id is
        # ignored/overridden, never trusted from the client.
        location_filter = user["location_id"]

    report = await _compute_weekly_totals(week_start, location_filter)
    return json_response(report)


@reports_bp.get("/weekly/export.csv")
@require_auth
@require_role("ADMIN")
async def weekly_export_csv(request):
    """
    V1 restricted to ADMIN per spec. Column layout is a FLAT placeholder
    (date, location, entry_type, category, weight_kg) -- PENDING validation
    against the real CSF spreadsheet template. See docs/CONTRACT.md and the
    root README's "Known assumptions" section.
    """
    raw_week_start = request.args.get("week_start")
    if not raw_week_start:
        return json_response(
            {"error": {"code": "VALIDATION_ERROR", "message": "week_start is required"}}, status=422
        )
    week_start, error = _validate_week_start(raw_week_start)
    if error:
        return json_response({"error": {"code": "VALIDATION_ERROR", "message": error}}, status=422)

    location_filter = request.args.get("location_id")
    week_end = week_start + timedelta(days=6)

    conditions = ["status = 'ACTIVE'", "collection_date >= $1", "collection_date <= $2"]
    params: list = [week_start, week_end]
    if location_filter:
        params.append(location_filter)
        conditions.append(f"location_id = ${len(params)}")

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT we.collection_date, l.name AS location_name, we.entry_type,
                       we.food_category_code, we.name, we.weight_kg
                FROM weigh_entries we
                JOIN locations l ON l.id = we.location_id
                WHERE {' AND '.join(conditions)}
                ORDER BY we.collection_date, l.name""",
            *params,
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Location", "Type", "Category", "Name", "Weight (kg)"])  # PLACEHOLDER layout
    for row in rows:
        writer.writerow(
            [
                row["collection_date"].isoformat(),
                row["location_name"],
                row["entry_type"],
                row["food_category_code"],
                row["name"],
                float(row["weight_kg"]),
            ]
        )

    response = text_response(buffer.getvalue(), content_type="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="csf-report-{week_start.isoformat()}.csv"'
    return response
