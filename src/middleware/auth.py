"""
Auth middleware and role-enforcement decorator. This is the ONE place
session validation happens -- every protected route relies on this having
run correctly, so it's the highest-leverage code in the whole API to get
right and to test thoroughly.

The session cookie is a signed JWT, but the JWT only proves WHO the caller
is. What they're allowed to do -- whether they're still active, their role,
their hub -- is read from the users table on every request. That way an
admin deactivating a user, changing their role or moving them to another
hub takes effect on that user's next request, not when their cookie
expires.
"""
import uuid
from functools import wraps

from sanic.response import json as json_response

from src.db.client import pool
from src.modules.auth.service import decode_session_jwt

SESSION_COOKIE_NAME = "csf_session"


def _unauthorized():
    return json_response(
        {"error": {"code": "UNAUTHORIZED", "message": "Sign-in required"}},
        status=401,
    )


def _session_user_id(request) -> uuid.UUID | None:
    """The user id from a valid session cookie, or None. Only proves
    identity -- never use the token's other claims for authorisation."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    claims = decode_session_jwt(token)
    if not claims:
        return None
    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError, TypeError):
        return None


def require_auth(handler):
    """Rejects the request with 401 unless the session cookie is valid AND
    it belongs to a user who is still active. On success, sets
    request.ctx.user to the user's CURRENT id, role and location_id, read
    from the database -- handlers use request.ctx.user, never the token."""

    @wraps(handler)
    async def wrapper(request, *args, **kwargs):
        user_id = _session_user_id(request)
        if user_id is None:
            return _unauthorized()

        async with pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, role, location_id FROM users WHERE id = $1 AND active = true",
                user_id,
            )
        if row is None:
            # Deactivated, or no longer exists.
            return _unauthorized()

        request.ctx.user = {
            "sub": str(row["id"]),
            "role": row["role"],
            "location_id": str(row["location_id"]) if row["location_id"] else None,
        }
        return await handler(request, *args, **kwargs)

    return wrapper


def require_role(*allowed_roles: str):
    """
    Stacks on top of require_auth. Usage:
        @require_auth
        @require_role("ADMIN", "FOOD_CENTRE")
        async def handler(request): ...
    """

    def decorator(handler):
        @wraps(handler)
        async def wrapper(request, *args, **kwargs):
            user = getattr(request.ctx, "user", None)
            if not user or user["role"] not in allowed_roles:
                return json_response(
                    {
                        "error": {
                            "code": "FORBIDDEN",
                            "message": "Not permitted for this role",
                        }
                    },
                    status=403,
                )
            return await handler(request, *args, **kwargs)

        return wrapper

    return decorator