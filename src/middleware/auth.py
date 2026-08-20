"""
Auth middleware and role-enforcement decorator. This is the ONE place
session validation happens -- every protected route relies on this having
run correctly, so it's the highest-leverage code in the whole API to get
right and to test thoroughly.
"""
from functools import wraps

from sanic.response import json as json_response

from src.modules.auth.service import decode_session_jwt

SESSION_COOKIE_NAME = "csf_session"


def get_current_user(request) -> dict | None:
    """Returns the decoded session payload, or None if not authenticated."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return decode_session_jwt(token)


def require_auth(handler):
    """Rejects the request with 401 if there's no valid session cookie."""

    @wraps(handler)
    async def wrapper(request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            return json_response(
                {"error": {"code": "UNAUTHORIZED", "message": "Sign-in required"}},
                status=401,
            )
        request.ctx.user = user
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
