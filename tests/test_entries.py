"""
Weigh entries: the role rules on who may record what and where, the
server-side net weight calculation, and the offline bulk-sync endpoint
the PWA's queue depends on.
"""
import uuid
from datetime import timedelta

import pytest

from tests.conftest import MONDAY, entry_payload

ENTRIES = "/api/v1/entries/"
BULK = "/api/v1/entries/bulk"


async def _count_entries(db):
    return await db.fetchval("SELECT count(*) FROM weigh_entries")


# --- Creating an entry: net weight -----------------------------------------
async def test_net_weight_is_gross_minus_trays(api, world):
    payload = entry_payload(
        world["hub"],
        gross_weight_kg=10.0,
        trays=[
            {"tray_type_code": "MEDIUM", "quantity": 2},  # 2 x 1.6
            {"tray_type_code": "HALF_SOLID", "quantity": 1},  # 1 x 1.0
        ],
    )

    res = await api.post(ENTRIES, user=world["hub_user"], json=payload)

    assert res.status == 201
    entry = res.json["entry"]
    assert entry["gross_weight_kg"] == 10.0
    assert entry["net_weight_kg"] == pytest.approx(5.8)
    assert {t["tray_type_code"]: t["quantity"] for t in entry["trays"]} == {
        "MEDIUM": 2,
        "HALF_SOLID": 1,
    }


async def test_net_weight_equals_gross_with_no_trays(api, world):
    res = await api.post(ENTRIES, user=world["hub_user"], json=entry_payload(world["hub"]))

    assert res.status == 201
    assert res.json["entry"]["net_weight_kg"] == 10.0


async def test_client_supplied_net_weight_is_ignored(api, world):
    payload = entry_payload(
        world["hub"],
        net_weight_kg=999,
        trays=[{"tray_type_code": "MEDIUM", "quantity": 1}],
    )

    res = await api.post(ENTRIES, user=world["hub_user"], json=payload)

    assert res.json["entry"]["net_weight_kg"] == pytest.approx(8.4)


async def test_trays_heavier_than_gross_are_rejected_and_nothing_saved(api, db, world):
    payload = entry_payload(
        world["hub"], gross_weight_kg=2.0, trays=[{"tray_type_code": "LARGE", "quantity": 2}]
    )

    res = await api.post(ENTRIES, user=world["hub_user"], json=payload)

    assert res.status == 422
    assert await _count_entries(db) == 0


@pytest.mark.parametrize(
    "trays",
    [
        [{"tray_type_code": "NOT_A_TRAY", "quantity": 1}],
        [{"tray_type_code": "MEDIUM", "quantity": 0}],
        [{"tray_type_code": "MEDIUM", "quantity": -1}],
        [{"tray_type_code": "MEDIUM", "quantity": "2"}],
        [{"tray_type_code": "MEDIUM", "quantity": 1}, {"tray_type_code": "MEDIUM", "quantity": 1}],
    ],
    ids=["unknown-code", "zero", "negative", "string-quantity", "duplicate-type"],
)
async def test_invalid_tray_selection_is_rejected(api, db, world, trays):
    res = await api.post(
        ENTRIES, user=world["hub_user"], json=entry_payload(world["hub"], trays=trays)
    )

    assert res.status == 422
    assert res.json["error"]["code"] == "VALIDATION_ERROR"
    assert await _count_entries(db) == 0


async def test_retired_tray_type_is_rejected(api, db, world):
    await db.execute("UPDATE tray_types SET active = false WHERE code = 'MEDIUM'")

    res = await api.post(
        ENTRIES,
        user=world["hub_user"],
        json=entry_payload(world["hub"], trays=[{"tray_type_code": "MEDIUM", "quantity": 1}]),
    )

    assert res.status == 422


# --- Creating an entry: validation -----------------------------------------
@pytest.mark.parametrize(
    "missing", ["entry_type", "location_id", "name", "food_category_code", "gross_weight_kg",
                "collection_date"]
)
async def test_required_fields(api, world, missing):
    payload = entry_payload(world["hub"])
    del payload[missing]

    res = await api.post(ENTRIES, user=world["hub_user"], json=payload)

    assert res.status == 422


async def test_collection_date_must_be_iso(api, world):
    res = await api.post(
        ENTRIES,
        user=world["hub_user"],
        json=entry_payload(world["hub"], collection_date="21/09/2026"),
    )

    assert res.status == 422


async def test_creating_requires_a_session(api, world):
    res = await api.post(ENTRIES, json=entry_payload(world["hub"]))

    assert res.status == 401


# --- Creating an entry: role rules -----------------------------------------
async def test_hub_user_cannot_record_at_another_hub(api, db, world):
    res = await api.post(ENTRIES, user=world["hub_user"], json=entry_payload(world["other_hub"]))

    assert res.status == 403
    assert await _count_entries(db) == 0


async def test_hub_user_cannot_record_out_entries(api, world):
    payload = entry_payload(
        world["hub"], entry_type="OUT", destination_location_id=world["centre"]
    )

    res = await api.post(ENTRIES, user=world["hub_user"], json=payload)

    assert res.status == 403


async def test_hub_in_entry_cannot_have_a_destination(api, world):
    payload = entry_payload(world["hub"], destination_location_id=world["centre"])

    res = await api.post(ENTRIES, user=world["hub_user"], json=payload)

    assert res.status == 422


async def test_food_centre_can_record_in_at_any_location(api, world):
    res = await api.post(
        ENTRIES, user=world["centre_user"], json=entry_payload(world["other_hub"])
    )

    assert res.status == 201


async def test_food_centre_out_entry_needs_a_destination(api, world):
    payload = entry_payload(world["centre"], entry_type="OUT")

    res = await api.post(ENTRIES, user=world["centre_user"], json=payload)

    assert res.status == 422


async def test_food_centre_can_record_out_with_destination(api, world):
    payload = entry_payload(world["centre"], entry_type="OUT", destination_location_id=world["hub"])

    res = await api.post(ENTRIES, user=world["centre_user"], json=payload)

    assert res.status == 201
    assert res.json["entry"]["destination_location_id"] == world["hub"]


async def test_database_constraints_backstop_admin_entries(api, db, world):
    """ADMIN has no application-level restrictions, so the CHECK
    constraints in the schema are the only thing stopping bad data."""
    no_destination = entry_payload(world["centre"], entry_type="OUT")
    to_itself = entry_payload(
        world["centre"], entry_type="OUT", destination_location_id=world["centre"]
    )

    for payload in (no_destination, to_itself):
        res = await api.post(ENTRIES, user=world["admin"], json=payload)
        assert res.status == 422

    assert await _count_entries(db) == 0


# --- Listing entries ------------------------------------------------------
async def _seed_entries(api, world):
    """One entry at each hub, recorded by the food centre."""
    for hub, name in ((world["hub"], "North collection"), (world["other_hub"], "South collection")):
        res = await api.post(
            ENTRIES, user=world["centre_user"], json=entry_payload(hub, name=name)
        )
        assert res.status == 201


async def test_hub_user_only_sees_their_own_hub(api, world):
    await _seed_entries(api, world)

    # Asking for the other hub explicitly must not work either.
    res = await api.get(
        ENTRIES, user=world["hub_user"], params={"location_id": world["other_hub"]}
    )

    assert res.status == 200
    assert [e["name"] for e in res.json["entries"]] == ["North collection"]


async def test_food_centre_can_filter_by_location(api, world):
    await _seed_entries(api, world)

    everything = await api.get(ENTRIES, user=world["centre_user"])
    south = await api.get(
        ENTRIES, user=world["centre_user"], params={"location_id": world["other_hub"]}
    )

    assert everything.json["pagination"]["total"] == 2
    assert [e["name"] for e in south.json["entries"]] == ["South collection"]


async def test_voided_entries_are_hidden_unless_asked_for(api, db, world):
    await _seed_entries(api, world)
    await db.execute("UPDATE weigh_entries SET status = 'VOID' WHERE name = 'South collection'")

    default = await api.get(ENTRIES, user=world["admin"])
    voided = await api.get(ENTRIES, user=world["admin"], params={"status": "VOID"})

    assert [e["name"] for e in default.json["entries"]] == ["North collection"]
    assert [e["name"] for e in voided.json["entries"]] == ["South collection"]


async def test_week_filter_covers_monday_to_sunday(api, world):
    for offset, name in ((-1, "previous Sunday"), (0, "Monday"), (6, "Sunday"), (7, "next Monday")):
        day = (MONDAY + timedelta(days=offset)).isoformat()
        await api.post(
            ENTRIES,
            user=world["hub_user"],
            json=entry_payload(world["hub"], name=name, collection_date=day),
        )

    res = await api.get(
        ENTRIES, user=world["hub_user"], params={"week_start": MONDAY.isoformat()}
    )

    assert sorted(e["name"] for e in res.json["entries"]) == ["Monday", "Sunday"]


async def test_listed_entries_include_their_trays(api, world):
    await api.post(
        ENTRIES,
        user=world["hub_user"],
        json=entry_payload(world["hub"], trays=[{"tray_type_code": "LARGE", "quantity": 3}]),
    )

    res = await api.get(ENTRIES, user=world["hub_user"])

    trays = res.json["entries"][0]["trays"]
    assert trays == [
        {"tray_type_code": "LARGE", "tray_type_name": "Large", "quantity": 3, "weight_kg": 1.9}
    ]


async def test_page_size_is_capped_at_100(api, world):
    res = await api.get(ENTRIES, user=world["admin"], params={"limit": 500})

    assert res.json["pagination"]["limit"] == 100


# --- Bulk sync (the offline queue) ---------------------------------------
def _queued(world, **overrides):
    return entry_payload(world["hub"], client_uuid=str(uuid.uuid4()), **overrides)


async def test_bulk_creates_every_entry(api, db, world):
    batch = [_queued(world), _queued(world, trays=[{"tray_type_code": "MEDIUM", "quantity": 1}])]

    res = await api.post(BULK, user=world["hub_user"], json={"entries": batch})

    assert res.status == 200
    assert [r["status"] for r in res.json["results"]] == ["created", "created"]
    assert await _count_entries(db) == 2
    net = await db.fetchval(
        "SELECT net_weight_kg FROM weigh_entries WHERE client_uuid = $1",
        uuid.UUID(batch[1]["client_uuid"]),
    )
    assert float(net) == pytest.approx(8.4)


async def test_resending_a_batch_is_idempotent(api, db, world):
    """The PWA retries the whole queue after a dropped connection. Entries
    the server already has must come back as duplicates, not new rows."""
    batch = [_queued(world), _queued(world)]
    first = await api.post(BULK, user=world["hub_user"], json={"entries": batch})

    retry = await api.post(BULK, user=world["hub_user"], json={"entries": batch})

    assert [r["status"] for r in retry.json["results"]] == ["duplicate", "duplicate"]
    assert [r["id"] for r in retry.json["results"]] == [r["id"] for r in first.json["results"]]
    assert await _count_entries(db) == 2


async def test_one_bad_entry_does_not_block_the_rest(api, db, world):
    good = _queued(world)
    too_many_trays = _queued(
        world, gross_weight_kg=1.0, trays=[{"tray_type_code": "LARGE", "quantity": 1}]
    )

    res = await api.post(BULK, user=world["hub_user"], json={"entries": [too_many_trays, good]})

    assert [r["status"] for r in res.json["results"]] == ["error", "created"]
    assert await _count_entries(db) == 1


async def test_bulk_applies_hub_role_rules(api, db, world):
    elsewhere = entry_payload(world["other_hub"], client_uuid=str(uuid.uuid4()))

    res = await api.post(BULK, user=world["hub_user"], json={"entries": [elsewhere]})

    assert res.json["results"][0]["status"] == "error"
    assert await _count_entries(db) == 0


async def test_bulk_reports_database_constraint_failures_per_entry(api, db, world):
    out_without_destination = entry_payload(
        world["centre"], entry_type="OUT", client_uuid=str(uuid.uuid4())
    )

    res = await api.post(
        BULK, user=world["centre_user"], json={"entries": [out_without_destination]}
    )

    assert res.status == 200
    assert res.json["results"][0]["status"] == "error"
    assert await _count_entries(db) == 0


async def test_bulk_requires_a_session(api, world):
    res = await api.post(BULK, json={"entries": [_queued(world)]})

    assert res.status == 401