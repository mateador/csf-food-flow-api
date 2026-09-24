"""
Weekly report and CSV export: Monday-to-Sunday totals on NET weight,
voided entries excluded, HUB users confined to their own hub, and CSV
restricted to ADMIN.
"""
import csv
import io
from datetime import timedelta

import pytest

from tests.conftest import MONDAY, entry_payload

WEEKLY = "/api/v1/reports/weekly"
CSV = "/api/v1/reports/weekly/export.csv"
ENTRIES = "/api/v1/entries/"


async def _record(api, user, location_id, **overrides):
    res = await api.post(ENTRIES, user=user, json=entry_payload(location_id, **overrides))
    assert res.status == 201, res.json
    return res.json["entry"]


# --- week_start validation --------------------------------------------------
@pytest.mark.parametrize("path", [WEEKLY, CSV])
@pytest.mark.parametrize(
    "params",
    [{}, {"week_start": "2026-09-22"}, {"week_start": "not-a-date"}],
    ids=["missing", "tuesday", "garbage"],
)
async def test_week_start_must_be_a_monday(api, world, path, params):
    res = await api.get(path, user=world["admin"], params=params)

    assert res.status == 422
    assert res.json["error"]["code"] == "VALIDATION_ERROR"


async def test_report_requires_a_session(api):
    res = await api.get(WEEKLY, params={"week_start": MONDAY.isoformat()})

    assert res.status == 401


# --- Totals ---------------------------------------------------------------
async def test_totals_use_net_weight(api, world):
    await _record(
        api,
        world["hub_user"],
        world["hub"],
        gross_weight_kg=10.0,
        trays=[{"tray_type_code": "MEDIUM", "quantity": 2}],  # net 6.8
    )
    await _record(api, world["hub_user"], world["hub"], gross_weight_kg=4.5)  # net 4.5

    res = await api.get(WEEKLY, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    assert res.status == 200
    assert res.json["totals"]["in_by_category"]["FRESH"] == 11.3


async def test_in_and_out_are_totalled_separately_per_location(api, world):
    await _record(api, world["centre_user"], world["hub"], food_category_code="BAKERY",
                  gross_weight_kg=3.0)
    await _record(api, world["centre_user"], world["centre"], entry_type="OUT",
                  destination_location_id=world["other_hub"], food_category_code="FROZEN",
                  gross_weight_kg=7.0)

    res = await api.get(WEEKLY, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    report = res.json
    assert report["week_start"] == "2026-09-21"
    assert report["week_end"] == "2026-09-27"
    assert report["totals"]["in_by_category"]["BAKERY"] == 3.0
    assert report["totals"]["out_by_category"]["FROZEN"] == 7.0
    by_location = {loc["location_name"]: loc for loc in report["by_location"]}
    assert by_location["North Hub"]["in_by_category"]["BAKERY"] == 3.0
    assert by_location["CSF Food Centre"]["out_by_category"]["FROZEN"] == 7.0


async def test_voided_and_out_of_week_entries_are_excluded(api, db, world):
    await _record(api, world["hub_user"], world["hub"], name="counted", gross_weight_kg=5.0)
    await _record(api, world["hub_user"], world["hub"], name="voided", gross_weight_kg=50.0)
    await _record(api, world["hub_user"], world["hub"], name="next week", gross_weight_kg=500.0,
                  collection_date=(MONDAY + timedelta(days=7)).isoformat())
    await db.execute("UPDATE weigh_entries SET status = 'VOID' WHERE name = 'voided'")

    res = await api.get(WEEKLY, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    assert res.json["totals"]["in_by_category"]["FRESH"] == 5.0


async def test_hub_user_only_gets_their_own_hub(api, world):
    await _record(api, world["centre_user"], world["hub"], gross_weight_kg=2.0)
    await _record(api, world["centre_user"], world["other_hub"], gross_weight_kg=20.0)

    res = await api.get(
        WEEKLY,
        user=world["hub_user"],
        params={"week_start": MONDAY.isoformat(), "location_id": world["other_hub"]},
    )

    assert res.json["totals"]["in_by_category"]["FRESH"] == 2.0
    assert [loc["location_name"] for loc in res.json["by_location"]] == ["North Hub"]


async def test_report_includes_newly_added_categories(api, db, world):
    """Regression: categories used to be a hardcoded list, and the first
    entry in a new category crashed the report with a KeyError."""
    await db.execute("INSERT INTO food_categories (code, name) VALUES ('TEST_DAIRY', 'Dairy')")
    await _record(api, world["hub_user"], world["hub"], food_category_code="TEST_DAIRY",
                  gross_weight_kg=6.0)

    res = await api.get(WEEKLY, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    assert res.status == 200
    assert res.json["totals"]["in_by_category"]["TEST_DAIRY"] == 6.0


async def test_report_survives_a_retired_category(api, db, world):
    await _record(api, world["hub_user"], world["hub"], food_category_code="BAKERY",
                  gross_weight_kg=6.0)
    await db.execute("UPDATE food_categories SET active = false WHERE code = 'BAKERY'")

    res = await api.get(WEEKLY, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    assert res.status == 200
    assert res.json["totals"]["in_by_category"]["BAKERY"] == 6.0


async def test_retired_categories_without_entries_are_left_out(api, db, world):
    await db.execute("UPDATE food_categories SET active = false WHERE code = 'BAKERY'")

    res = await api.get(WEEKLY, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    assert "BAKERY" not in res.json["totals"]["in_by_category"]
    assert res.json["totals"]["in_by_category"]["FRESH"] == 0.0


async def test_totals_are_exact_to_two_decimal_places(api, world):
    """Summing 0.1 and 0.2 as floats gives 0.30000000000000004, which the
    report page would display as-is."""
    await _record(api, world["hub_user"], world["hub"], gross_weight_kg=0.1)
    await _record(api, world["hub_user"], world["hub"], gross_weight_kg=0.2)

    res = await api.get(WEEKLY, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    assert res.json["totals"]["in_by_category"]["FRESH"] == 0.3
    by_location = res.json["by_location"][0]
    assert by_location["in_by_category"]["FRESH"] == 0.3


# --- CSV export -----------------------------------------------------------
@pytest.mark.parametrize("role", ["hub_user", "centre_user"])
async def test_csv_export_is_admin_only(api, world, role):
    res = await api.get(CSV, user=world[role], params={"week_start": MONDAY.isoformat()})

    assert res.status == 403


async def test_csv_export_rows(api, world):
    await _record(
        api,
        world["hub_user"],
        world["hub"],
        name="Tesco Newmarket Road",
        gross_weight_kg=10.0,
        trays=[{"tray_type_code": "MEDIUM", "quantity": 2}],
    )

    res = await api.get(CSV, user=world["admin"], params={"week_start": MONDAY.isoformat()})

    assert res.status == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert 'filename="csf-report-2026-09-21.csv"' in res.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(res.text)))
    assert rows == [
        ["Date", "Location", "Type", "Name", "Category", "Gross Weight (kg)", "Trays",
         "Net Weight (kg)"],
        ["2026-09-21", "North Hub", "IN", "Tesco Newmarket Road", "FRESH", "10.0",
         "2x Medium", "6.8"],
    ]