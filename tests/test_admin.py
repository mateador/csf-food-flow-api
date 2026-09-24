"""
Admin-only management of users and locations, plus the reference-data
endpoints every signed-in user reads (categories, tray types).
"""
import uuid

import pytest

USERS = "/api/v1/users/"
LOCATIONS = "/api/v1/locations/"


# --- Role gating ----------------------------------------------------------
@pytest.mark.parametrize("role", ["hub_user", "centre_user"])
@pytest.mark.parametrize(
    "method, path, body",
    [
        ("get", USERS, None),
        ("post", USERS, {"name": "X", "email": "x@example.org", "role": "ADMIN"}),
        ("patch", f"{USERS}{uuid.uuid4()}", {"active": False}),
        ("post", LOCATIONS, {"name": "New Hub", "type": "HUB"}),
        ("patch", f"{LOCATIONS}{uuid.uuid4()}", {"active": False}),
    ],
    ids=["list-users", "create-user", "update-user", "create-location", "update-location"],
)
async def test_admin_endpoints_refuse_other_roles(api, world, role, method, path, body):
    kwargs = {"json": body} if body is not None else {}

    res = await getattr(api, method)(path, user=world[role], **kwargs)

    assert res.status == 403
    assert res.json["error"]["code"] == "FORBIDDEN"


# --- Users ----------------------------------------------------------------
async def test_admin_creates_a_hub_user_with_lowercased_email(api, world):
    res = await api.post(
        USERS,
        user=world["admin"],
        json={"name": "Sam", "email": "Sam@Example.ORG", "role": "HUB",
              "location_id": world["hub"]},
    )

    assert res.status == 201
    assert res.json["email"] == "sam@example.org"
    assert res.json["location_id"] == world["hub"]
    assert res.json["active"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Sam", "email": "sam@example.org", "role": "HUB"},
        {"name": "Sam", "email": "sam@example.org", "role": "VOLUNTEER"},
        {"email": "sam@example.org", "role": "ADMIN"},
    ],
    ids=["hub-without-location", "unknown-role", "missing-name"],
)
async def test_invalid_users_are_rejected(api, world, body):
    res = await api.post(USERS, user=world["admin"], json=body)

    assert res.status == 422


async def test_duplicate_email_is_rejected(api, world):
    body = {"name": "Sam", "email": "sam@example.org", "role": "ADMIN"}
    await api.post(USERS, user=world["admin"], json=body)

    res = await api.post(USERS, user=world["admin"], json={**body, "email": "SAM@example.org"})

    assert res.status == 422


async def test_admin_deactivates_a_user(api, world):
    res = await api.patch(
        f"{USERS}{world['hub_user']['id']}", user=world["admin"], json={"active": False}
    )

    assert res.status == 200
    assert res.json["active"] is False

    listed = await api.get(USERS, user=world["admin"], params={"active": "false"})
    assert [u["id"] for u in listed.json] == [str(world["hub_user"]["id"])]


async def test_updating_an_unknown_user_is_404(api, world):
    res = await api.patch(f"{USERS}{uuid.uuid4()}", user=world["admin"], json={"name": "X"})

    assert res.status == 404


async def test_update_with_no_fields_is_rejected(api, world):
    res = await api.patch(f"{USERS}{world['hub_user']['id']}", user=world["admin"], json={})

    assert res.status == 422


# --- Locations --------------------------------------------------------------
async def test_hub_user_only_sees_their_own_location(api, world):
    res = await api.get(LOCATIONS, user=world["hub_user"])

    assert [loc["id"] for loc in res.json] == [world["hub"]]


async def test_food_centre_sees_all_locations_and_can_filter(api, world):
    everything = await api.get(LOCATIONS, user=world["centre_user"])
    hubs = await api.get(LOCATIONS, user=world["centre_user"], params={"type": "HUB"})

    assert len(everything.json) == 3
    assert sorted(loc["name"] for loc in hubs.json) == ["North Hub", "South Hub"]


async def test_admin_creates_and_retires_a_location(api, world):
    created = await api.post(
        LOCATIONS, user=world["admin"], json={"name": "East Hub", "type": "HUB"}
    )
    retired = await api.patch(
        f"{LOCATIONS}{created.json['id']}", user=world["admin"], json={"active": False}
    )
    active = await api.get(LOCATIONS, user=world["admin"], params={"active": "true"})

    assert created.status == 201
    assert retired.json["active"] is False
    assert "East Hub" not in [loc["name"] for loc in active.json]


async def test_location_type_must_be_valid(api, world):
    res = await api.post(LOCATIONS, user=world["admin"], json={"name": "Shop", "type": "SHOP"})

    assert res.status == 422


async def test_updating_an_unknown_location_is_404(api, world):
    res = await api.patch(f"{LOCATIONS}{uuid.uuid4()}", user=world["admin"], json={"name": "X"})

    assert res.status == 404


# --- Reference data -------------------------------------------------------
async def test_categories_lists_active_categories_only(api, db, world):
    await db.execute("UPDATE food_categories SET active = false WHERE code = 'BAKERY'")

    res = await api.get("/api/v1/categories/", user=world["hub_user"])

    codes = [c["code"] for c in res.json]
    assert "BAKERY" not in codes
    assert {"FRESH", "FROZEN", "AMBIENT", "VEG_FRUIT", "OTHER_FRESH"} <= set(codes)


async def test_tray_types_come_with_their_weights(api, world):
    res = await api.get("/api/v1/tray-types/", user=world["hub_user"])

    weights = {t["code"]: t["weight_kg"] for t in res.json}
    assert len(weights) == 8
    assert weights["MEDIUM"] == 1.6
    assert weights["HALF_SOLID"] == 1.0


@pytest.mark.parametrize("path", ["/api/v1/categories/", "/api/v1/tray-types/", LOCATIONS])
async def test_reference_data_requires_a_session(api, path):
    res = await api.get(path)

    assert res.status == 401