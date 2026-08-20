"""
Auth service: magic-link token generation/validation, and JWT session
issuance. No passwords anywhere in this module -- see the User schema note
in the spec, password_hash was deliberately removed.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from src.db.client import pool

MAGIC_LINK_TTL_MINUTES = int(os.environ.get("MAGIC_LINK_TTL_MINUTES", "15"))
ACCESS_TOKEN_TTL_MINUTES = int(os.environ.get("ACCESS_TOKEN_TTL_MINUTES", "720"))
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"


def _hash_token(raw_token: str) -> str:
    # Tokens are single-use and short-lived, so a fast hash (not bcrypt) is
    # the right call here -- this isn't a password, it's a one-time nonce.
    # sha256 is enough to make the stored value useless if the DB leaks,
    # while keeping verification cheap.
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def create_magic_link_token(user_id: str) -> str:
    """Returns the RAW token (only time it ever exists in plaintext)."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_TTL_MINUTES)

    async with pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO magic_link_tokens (user_id, token_hash, expires_at)
               VALUES ($1, $2, $3)""",
            user_id,
            token_hash,
            expires_at,
        )
    return raw_token


async def verify_and_consume_magic_link_token(raw_token: str) -> dict | None:
    """
    Validates a token, marks it used, updates last_login_at, all in one
    transaction. Returns the user row on success, None if the token is
    invalid/expired/already-used.
    """
    token_hash = _hash_token(raw_token)

    async with pool().acquire() as conn:
        async with conn.transaction():
            token_row = await conn.fetchrow(
                """SELECT id, user_id, expires_at, used_at
                   FROM magic_link_tokens
                   WHERE token_hash = $1
                   FOR UPDATE""",
                token_hash,
            )
            if not token_row:
                return None
            if token_row["used_at"] is not None:
                return None
            if token_row["expires_at"] < datetime.now(timezone.utc):
                return None

            await conn.execute(
                "UPDATE magic_link_tokens SET used_at = now() WHERE id = $1",
                token_row["id"],
            )

            user_row = await conn.fetchrow(
                "SELECT * FROM users WHERE id = $1 AND active = true",
                token_row["user_id"],
            )
            if not user_row:
                return None

            await conn.execute(
                "UPDATE users SET last_login_at = now() WHERE id = $1",
                user_row["id"],
            )

            return dict(user_row)


def issue_session_jwt(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "location_id": str(user["location_id"]) if user["location_id"] else None,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
