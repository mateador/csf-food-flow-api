"""
Login-code auth and session handling. A 4-digit code has only 10,000
possible values, so the security of sign-in rests on the limits around
it -- expiry, single use, attempt lockout, request throttling -- and on
never revealing which emails have accounts. Each of those gets a test.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

import src.modules.auth.routes as auth_routes
import src.modules.auth.service as auth_service
from src.middleware.auth import SESSION_COOKIE_NAME

REQUEST = "/api/v1/auth/code/request"
VERIFY = "/api/v1/auth/code/verify"


@pytest.fixture
def sent_codes(monkeypatch):
    """Captures every login code the API tries to email, instead of sending."""
    sent = []

    async def fake_send(to_email, code):
        sent.append((to_email, code))

    monkeypatch.setattr(auth_routes, "send_login_code_email", fake_send)
    return sent


@pytest.fixture
def fixed_codes(monkeypatch):
    """Makes the generated codes predictable: pass the sequence of values
    secrets.randbelow() should return."""

    def _fix(*values):
        it = iter(values)
        monkeypatch.setattr(auth_service.secrets, "randbelow", lambda _n: next(it))

    return _fix


def _wrong(code):
    return f"{(int(code) + 1) % 10000:04d}"


def _session_token(response):
    """Pulls the session JWT out of a Set-Cookie header."""
    cookie = response.headers.get("set-cookie", "")
    prefix = f"{SESSION_COOKIE_NAME}="
    assert prefix in cookie, f"no session cookie set: {cookie!r}"
    return cookie.split(prefix, 1)[1].split(";", 1)[0]


# --- Requesting a code ------------------------------------------------------
async def test_request_sends_a_four_digit_code(api, make_user, sent_codes):
    user = await make_user("ADMIN", email="alex@example.org")

    res = await api.post(REQUEST, json={"email": "alex@example.org"})

    assert res.status == 200
    assert res.json == {"status": "requested"}
    assert len(sent_codes) == 1
    email, code = sent_codes[0]
    assert email == user["email"]
    assert len(code) == 4 and code.isdigit()


async def test_request_normalises_email_case_and_whitespace(api, make_user, sent_codes):
    await make_user("ADMIN", email="alex@example.org")

    await api.post(REQUEST, json={"email": "  Alex@Example.ORG "})

    assert [e for e, _ in sent_codes] == ["alex@example.org"]


@pytest.mark.parametrize("email", ["nobody@example.org", "", None])
async def test_request_gives_the_same_response_for_unknown_emails(api, sent_codes, email):
    res = await api.post(REQUEST, json={"email": email})

    assert res.status == 200
    assert res.json == {"status": "requested"}
    assert sent_codes == []


async def test_request_sends_nothing_to_a_deactivated_user(api, make_user, sent_codes):
    await make_user("ADMIN", email="gone@example.org", active=False)

    res = await api.post(REQUEST, json={"email": "gone@example.org"})

    assert res.status == 200
    assert res.json == {"status": "requested"}
    assert sent_codes == []


async def test_requests_are_throttled_to_three_per_window(
    api, make_user, sent_codes, fixed_codes
):
    fixed_codes(1111, 2222, 3333)
    await make_user("ADMIN", email="alex@example.org")

    responses = [await api.post(REQUEST, json={"email": "alex@example.org"}) for _ in range(4)]

    # The fourth request looks identical to the caller, but sends nothing.
    assert all(r.status == 200 and r.json == {"status": "requested"} for r in responses)
    assert len(sent_codes) == 3


async def test_codes_stored_as_hashes_not_plaintext(api, db, make_user, sent_codes):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]

    stored = await db.fetchval("SELECT code_hash FROM login_codes")

    assert code not in stored
    assert len(stored) == 64  # SHA-256 hex digest


# --- Verifying a code -------------------------------------------------------
async def test_correct_code_signs_in_and_sets_session_cookie(api, db, make_user, sent_codes):
    user = await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]

    res = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})

    assert res.status == 200
    assert res.json["user"]["id"] == str(user["id"])
    assert res.json["user"]["role"] == "ADMIN"
    cookie = res.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert await db.fetchval("SELECT last_login_at FROM users WHERE id = $1", user["id"])


async def test_session_cookie_from_sign_in_works_on_me(api, make_user, sent_codes):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]
    signed_in = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})

    token = _session_token(signed_in)
    res = await api.get("/api/v1/me", headers={"cookie": f"{SESSION_COOKIE_NAME}={token}"})

    assert res.status == 200
    assert res.json["email"] == "alex@example.org"


async def test_session_cookie_is_secure_in_production(api, make_user, sent_codes, monkeypatch):
    monkeypatch.setattr(auth_routes, "IS_PRODUCTION", True)
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]

    res = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})

    assert "Secure" in res.headers["set-cookie"]


async def test_code_can_only_be_used_once(api, make_user, sent_codes):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]

    first = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})
    second = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})

    assert first.status == 200
    assert second.status == 401
    assert second.json["error"]["code"] == "INVALID_CODE"


async def test_wrong_code_is_rejected(api, make_user, sent_codes):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]

    res = await api.post(VERIFY, json={"email": "alex@example.org", "code": _wrong(code)})

    assert res.status == 401
    assert res.json["error"]["code"] == "INVALID_CODE"
    assert "set-cookie" not in res.headers


async def test_five_wrong_guesses_lock_the_code(api, make_user, sent_codes):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]

    for _ in range(5):
        res = await api.post(VERIFY, json={"email": "alex@example.org", "code": _wrong(code)})
        assert res.json["error"]["code"] == "INVALID_CODE"

    # Even the right code is refused once the attempt budget is spent.
    res = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})
    assert res.status == 401
    assert res.json["error"]["code"] == "TOO_MANY_ATTEMPTS"


async def test_expired_code_is_rejected(api, db, make_user, sent_codes):
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]
    await db.execute("UPDATE login_codes SET expires_at = now() - interval '1 minute'")

    res = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})

    assert res.status == 401
    assert res.json["error"]["code"] == "EXPIRED"


async def test_new_code_invalidates_the_previous_one(api, make_user, sent_codes, fixed_codes):
    fixed_codes(1111, 2222)
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    await api.post(REQUEST, json={"email": "alex@example.org"})

    old = await api.post(VERIFY, json={"email": "alex@example.org", "code": "1111"})
    new = await api.post(VERIFY, json={"email": "alex@example.org", "code": "2222"})

    assert old.status == 401
    assert new.status == 200


async def test_deactivated_user_cannot_sign_in_with_an_issued_code(api, db, make_user, sent_codes):
    user = await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    _, code = sent_codes[0]
    await db.execute("UPDATE users SET active = false WHERE id = $1", user["id"])

    res = await api.post(VERIFY, json={"email": "alex@example.org", "code": code})

    assert res.status == 401


@pytest.mark.parametrize("body", [{}, {"email": "alex@example.org"}, {"code": "1234"}])
async def test_verify_requires_email_and_code(api, body):
    res = await api.post(VERIFY, json=body)

    assert res.status == 422
    assert res.json["error"]["code"] == "VALIDATION_ERROR"


async def test_same_code_value_can_be_issued_to_a_user_twice(
    api, make_user, sent_codes, fixed_codes
):
    """With only 10,000 possible codes, a regular user will eventually be
    issued a code they've had before. That must not break sign-in."""
    fixed_codes(1234, 1234)
    await make_user("ADMIN", email="alex@example.org")
    await api.post(REQUEST, json={"email": "alex@example.org"})
    await api.post(VERIFY, json={"email": "alex@example.org", "code": "1234"})

    res = await api.post(REQUEST, json={"email": "alex@example.org"})
    signed_in = await api.post(VERIFY, json={"email": "alex@example.org", "code": "1234"})

    assert res.status == 200
    assert len(sent_codes) == 2
    assert signed_in.status == 200


# --- Sessions ---------------------------------------------------------------
async def test_me_requires_a_session(api):
    res = await api.get("/api/v1/me")

    assert res.status == 401
    assert res.json["error"]["code"] == "UNAUTHORIZED"


async def test_expired_session_is_rejected(api, make_user):
    user = await make_user("ADMIN")
    token = jwt.encode(
        {
            "sub": str(user["id"]),
            "role": "ADMIN",
            "location_id": None,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        auth_service.JWT_SECRET,
        algorithm="HS256",
    )

    res = await api.get("/api/v1/me", headers={"cookie": f"{SESSION_COOKIE_NAME}={token}"})

    assert res.status == 401


async def test_session_signed_with_another_secret_is_rejected(api, make_user):
    """A forged cookie claiming ADMIN must not work without the real secret."""
    user = await make_user("FOOD_CENTRE")
    token = jwt.encode(
        {
            "sub": str(user["id"]),
            "role": "ADMIN",
            "location_id": None,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "not-the-real-secret",
        algorithm="HS256",
    )

    res = await api.get("/api/v1/users/", headers={"cookie": f"{SESSION_COOKIE_NAME}={token}"})

    assert res.status == 401


async def test_deactivated_user_loses_access_immediately(api, db, make_user):
    user = await make_user("ADMIN")
    await db.execute("UPDATE users SET active = false WHERE id = $1", user["id"])

    res = await api.get("/api/v1/users/", user=user)

    assert res.status == 401


async def test_role_change_takes_effect_on_the_next_request(api, db, make_user):
    """The cookie still says ADMIN; the database now says HUB. The database wins."""
    user = await make_user("ADMIN")
    location = await db.fetchval(
        "INSERT INTO locations (name, type) VALUES ('Moved Hub', 'HUB') RETURNING id"
    )
    await db.execute(
        "UPDATE users SET role = 'HUB', location_id = $2 WHERE id = $1", user["id"], location
    )

    res = await api.get("/api/v1/users/", user=user)

    assert res.status == 403


async def test_hub_reassignment_takes_effect_on_the_next_request(api, db, world):
    """A hub user moved to another hub records at the new hub straight away,
    and can no longer record at the old one."""
    from tests.conftest import entry_payload

    hub_user = world["hub_user"]  # session issued while assigned to world["hub"]
    await db.execute(
        "UPDATE users SET location_id = $2 WHERE id = $1", hub_user["id"], world["other_hub"]
    )

    old = await api.post("/api/v1/entries/", user=hub_user, json=entry_payload(world["hub"]))
    new = await api.post(
        "/api/v1/entries/", user=hub_user, json=entry_payload(world["other_hub"])
    )

    assert old.status == 403
    assert new.status == 201


async def test_session_for_a_user_that_does_not_exist_is_rejected(api):
    ghost = {"id": uuid.uuid4(), "role": "ADMIN", "location_id": None}

    res = await api.get("/api/v1/users/", user=ghost)

    assert res.status == 401

# --- Sign-out -------------------------------------------------------------
LOGOUT = "/api/v1/auth/logout"


async def test_logout_expires_the_session_cookie(api, world):
    res = await api.post(LOGOUT, user=world["hub_user"])

    assert res.status == 200
    assert res.json == {"status": "signed_out"}
    cookie = res.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "Max-Age=0" in cookie
    assert "Path=/" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


async def test_logout_cookie_is_secure_in_production(api, monkeypatch):
    monkeypatch.setattr(auth_routes, "IS_PRODUCTION", True)

    res = await api.post(LOGOUT)

    assert "Secure" in res.headers["set-cookie"]


async def test_logout_without_a_session_still_succeeds(api):
    res = await api.post(LOGOUT)

    assert res.status == 200
    assert "Max-Age=0" in res.headers["set-cookie"]