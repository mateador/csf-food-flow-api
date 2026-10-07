"""
PIN login: email + a 4-digit PIN as a long-lived credential, replacing
the emailed one-time code as the PWA's default sign-in flow. By explicit
product decision there is no expiry and no attempt lockout here -- this
suite documents that decision (see test_wrong_pin_never_locks_out) rather
than treating it as a gap to quietly patch.
"""
import pytest

from src.middleware.auth import SESSION_COOKIE_NAME

CHECK = "/api/v1/auth/pin/check"
SET = "/api/v1/auth/pin/set"
LOGIN = "/api/v1/auth/pin/login"
CHANGE = "/api/v1/auth/pin"


def _session_token(response):
    cookie = response.headers.get("set-cookie", "")
    prefix = f"{SESSION_COOKIE_NAME}="
    assert prefix in cookie, f"no session cookie set: {cookie!r}"
    return cookie.split(prefix, 1)[1].split(";", 1)[0]


# --- Checking status ---------------------------------------------------
async def test_fresh_user_needs_setup(api, make_user):
    await make_user("ADMIN", email="alex@example.org")

    res = await api.post(CHECK, json={"email": "alex@example.org"})

    assert res.status == 200
    assert res.json == {"needs_setup": True}


async def test_user_with_a_pin_does_not_need_setup(api, make_user):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    res = await api.post(CHECK, json={"email": "alex@example.org"})

    assert res.json == {"needs_setup": False}


@pytest.mark.parametrize("email", ["nobody@example.org", "", None])
async def test_unknown_or_missing_email_does_not_need_setup(api, email):
    res = await api.post(CHECK, json={"email": email})

    assert res.status in (200, 422)
    if res.status == 200:
        assert res.json == {"needs_setup": False}


async def test_deactivated_user_does_not_need_setup(api, make_user):
    await make_user("ADMIN", email="gone@example.org", active=False)

    res = await api.post(CHECK, json={"email": "gone@example.org"})

    assert res.json == {"needs_setup": False}


# --- First-time setup ----------------------------------------------------
async def test_setup_signs_in_and_sets_session_cookie(api, db, make_user):
    user = await make_user("ADMIN", email="alex@example.org")

    res = await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    assert res.status == 200
    assert res.json["user"]["id"] == str(user["id"])
    cookie = res.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert await db.fetchval("SELECT pin_hash FROM users WHERE id = $1", user["id"]) is not None


async def test_setup_rejects_mismatched_confirmation(api, db, make_user):
    user = await make_user("ADMIN", email="alex@example.org")

    res = await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "5678"})

    assert res.status == 422
    assert await db.fetchval("SELECT pin_hash FROM users WHERE id = $1", user["id"]) is None


@pytest.mark.parametrize("pin", ["123", "12345", "abcd", "", None])
async def test_setup_rejects_a_pin_that_is_not_four_digits(api, make_user, pin):
    await make_user("ADMIN", email="alex@example.org")

    res = await api.post(SET, json={"email": "alex@example.org", "pin": pin, "pin_confirm": pin})

    assert res.status == 422


async def test_setup_cannot_run_twice(api, make_user):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    res = await api.post(SET, json={"email": "alex@example.org", "pin": "5678", "pin_confirm": "5678"})

    assert res.status == 409
    assert res.json["error"]["code"] == "PIN_ALREADY_SET"


async def test_setup_for_unknown_email_fails_the_same_way(api):
    res = await api.post(SET, json={"email": "nobody@example.org", "pin": "1234", "pin_confirm": "1234"})

    assert res.status == 409


# --- Logging in ------------------------------------------------------------
async def test_correct_pin_signs_in(api, make_user):
    user = await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    res = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "1234"})

    assert res.status == 200
    assert res.json["user"]["id"] == str(user["id"])


async def test_wrong_pin_is_rejected(api, make_user):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    res = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "9999"})

    assert res.status == 401
    assert res.json["error"]["code"] == "INVALID_PIN"
    assert "set-cookie" not in res.headers


async def test_wrong_pin_never_locks_out(api, make_user):
    """By explicit product decision there is no attempt lockout on PIN
    login, unlike the emailed-code flow -- this documents that choice."""
    await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    for _ in range(20):
        res = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "0000"})
        assert res.status == 401

    # The real PIN still works after 20 wrong guesses.
    res = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "1234"})
    assert res.status == 200


async def test_login_before_setup_is_rejected(api, make_user):
    await make_user("ADMIN", email="alex@example.org")

    res = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "1234"})

    assert res.status == 401
    assert res.json["error"]["code"] == "INVALID_PIN"


async def test_deactivated_user_cannot_sign_in_with_a_correct_pin(api, db, make_user):
    user = await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})
    await db.execute("UPDATE users SET active = false WHERE id = $1", user["id"])

    res = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "1234"})

    assert res.status == 401


async def test_session_from_pin_login_works_on_me(api, make_user):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})
    signed_in = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "1234"})

    token = _session_token(signed_in)
    res = await api.get("/api/v1/me", headers={"cookie": f"{SESSION_COOKIE_NAME}={token}"})

    assert res.status == 200
    assert res.json["email"] == "alex@example.org"


# --- Self-service change ----------------------------------------------------
async def test_change_pin_requires_a_session(api):
    res = await api.patch(CHANGE, json={"current_pin": "1234", "pin": "5678", "pin_confirm": "5678"})

    assert res.status == 401


async def test_change_pin_succeeds_with_the_correct_current_pin(api, make_user):
    user = await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    res = await api.patch(
        CHANGE, user=user, json={"current_pin": "1234", "pin": "5678", "pin_confirm": "5678"}
    )

    assert res.status == 200
    assert res.json == {"status": "changed"}
    login = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "5678"})
    assert login.status == 200


async def test_change_pin_rejects_the_wrong_current_pin(api, make_user):
    user = await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    res = await api.patch(
        CHANGE, user=user, json={"current_pin": "0000", "pin": "5678", "pin_confirm": "5678"}
    )

    assert res.status == 401
    still_works = await api.post(LOGIN, json={"email": "alex@example.org", "pin": "1234"})
    assert still_works.status == 200


async def test_change_pin_rejects_mismatched_new_pin(api, make_user):
    user = await make_user("ADMIN", email="alex@example.org")
    await api.post(SET, json={"email": "alex@example.org", "pin": "1234", "pin_confirm": "1234"})

    res = await api.patch(
        CHANGE, user=user, json={"current_pin": "1234", "pin": "5678", "pin_confirm": "0000"}
    )

    assert res.status == 422


# --- Admin reset -------------------------------------------------------
RESET = "/api/v1/users/{}/pin"


async def test_admin_can_reset_any_users_pin(api, db, world):
    hub_user = world["hub_user"]
    await api.post(SET, json={"email": hub_user["email"], "pin": "1234", "pin_confirm": "1234"})

    res = await api.patch(
        RESET.format(hub_user["id"]),
        user=world["admin"],
        json={"pin": "5678", "pin_confirm": "5678"},
    )

    assert res.status == 200
    login = await api.post(LOGIN, json={"email": hub_user["email"], "pin": "5678"})
    assert login.status == 200


async def test_admin_reset_does_not_require_the_old_pin(api, world):
    """This IS the recovery path for a forgotten PIN -- no old-pin check."""
    hub_user = world["hub_user"]
    # hub_user never set a PIN at all.

    res = await api.patch(
        RESET.format(hub_user["id"]),
        user=world["admin"],
        json={"pin": "5678", "pin_confirm": "5678"},
    )

    assert res.status == 200
    login = await api.post(LOGIN, json={"email": hub_user["email"], "pin": "5678"})
    assert login.status == 200


async def test_admin_can_reset_another_admins_pin(api, world, make_user):
    other_admin = await make_user("ADMIN", email="other-admin@example.org")

    res = await api.patch(
        RESET.format(other_admin["id"]),
        user=world["admin"],
        json={"pin": "5678", "pin_confirm": "5678"},
    )

    assert res.status == 200


async def test_non_admin_cannot_reset_a_pin(api, world):
    res = await api.patch(
        RESET.format(world["admin"]["id"]),
        user=world["hub_user"],
        json={"pin": "5678", "pin_confirm": "5678"},
    )

    assert res.status == 403


async def test_admin_reset_requires_a_session(api, world):
    res = await api.patch(
        RESET.format(world["hub_user"]["id"]), json={"pin": "5678", "pin_confirm": "5678"}
    )

    assert res.status == 401


async def test_admin_reset_of_unknown_user_is_not_found(api, world):
    res = await api.patch(
        RESET.format("00000000-0000-0000-0000-000000000000"),
        user=world["admin"],
        json={"pin": "5678", "pin_confirm": "5678"},
    )

    assert res.status == 404


async def test_admin_reset_rejects_mismatched_confirmation(api, world):
    res = await api.patch(
        RESET.format(world["hub_user"]["id"]),
        user=world["admin"],
        json={"pin": "5678", "pin_confirm": "0000"},
    )

    assert res.status == 422


async def test_admin_reset_is_recorded_in_the_audit_log(api, db, world):
    hub_user = world["hub_user"]

    await api.patch(
        RESET.format(hub_user["id"]),
        user=world["admin"],
        json={"pin": "5678", "pin_confirm": "5678"},
    )

    row = await db.fetchrow("SELECT * FROM audit_log WHERE entity_id = $1", hub_user["id"])
    assert row is not None
    assert row["entity_type"] == "USER"
    assert row["action"] == "UPDATE"
    assert row["user_id"] == world["admin"]["id"]
