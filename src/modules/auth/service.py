"""
Auth service: login-code generation/validation, and JWT session issuance.
No passwords anywhere in this module -- see the User schema note in the
spec, password_hash was deliberately removed.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from src.db.client import pool

LOGIN_CODE_TTL_MINUTES = int(os.environ.get("LOGIN_CODE_TTL_MINUTES", "10"))
ACCESS_TOKEN_TTL_MINUTES = int(os.environ.get("ACCESS_TOKEN_TTL_MINUTES", "720"))
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"

# A 4-digit code has only 10,000 possible values -- unlike the old random
# token, this needs real brute-force protection, not just a fast hash.
MAX_CODE_ATTEMPTS = 5
MAX_CODE_REQUESTS_PER_WINDOW = 3
CODE_REQUEST_WINDOW_MINUTES = 10

# How long a code row is kept after it expires. Nothing needs old rows: the
# request throttle looks back CODE_REQUEST_WINDOW_MINUTES, and lockout only
# concerns the one active code. A day is kept anyway so a "why didn't my
# code work?" question can still be answered from the table the next day.
LOGIN_CODE_RETENTION_HOURS = 24


def _hash_code(email: str, code: str) -> str:
    # Salted with the email so the same 4-digit code for two different
    # users never produces the same hash. This does NOT make brute-forcing
    # a single row computationally hard (10,000 hashes is trivial to
    # compute either way) -- it only prevents one precomputed table from
    # working against every user's row at once. The real defenses against
    # brute force are attempts-lockout and expiry, not this hash.
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


async def can_request_new_code(user_id: str) -> bool:
    """Rate limit on REQUESTS, not attempts -- stops someone from resetting
    their own attempts budget by just requesting a fresh code repeatedly."""
    async with pool().acquire() as conn:
        count = await conn.fetchval(
            """SELECT count(*) FROM login_codes
               WHERE user_id = $1
                 AND created_at > now() - make_interval(mins => $2)""",
            user_id,
            CODE_REQUEST_WINDOW_MINUTES,
        )
    return count < MAX_CODE_REQUESTS_PER_WINDOW


async def create_login_code(user_id: str, email: str) -> str:
    """Returns the RAW 4-digit code (only time it ever exists in plaintext)."""
    raw_code = f"{secrets.randbelow(10000):04d}"
    code_hash = _hash_code(email, raw_code)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=LOGIN_CODE_TTL_MINUTES)

    async with pool().acquire() as conn:
        async with conn.transaction():
            # Only one code should ever be valid at a time -- invalidate
            # anything still active for this user before issuing a new one.
            await conn.execute(
                "UPDATE login_codes SET used_at = now() WHERE user_id = $1 AND used_at IS NULL",
                user_id,
            )
            await conn.execute(
                """INSERT INTO login_codes (user_id, code_hash, expires_at)
                   VALUES ($1, $2, $3)""",
                user_id,
                code_hash,
                expires_at,
            )
            # Housekeeping, for every user at once: without it the table
            # grows by one row per sign-in forever. Done here rather than
            # in a scheduled job because issuing a code is the only thing
            # that adds rows, so the table can't grow without this running.
            # One indexed delete (idx_login_codes_expires_at).
            await conn.execute(
                """DELETE FROM login_codes
                   WHERE expires_at < now() - make_interval(hours => $1)""",
                LOGIN_CODE_RETENTION_HOURS,
            )
    return raw_code


async def verify_and_consume_login_code(email: str, raw_code: str) -> dict | str:
    """
    Returns:
        dict                 -- the user row, on success
        "INVALID_CODE"       -- wrong code, or no active code exists at all
        "TOO_MANY_ATTEMPTS"  -- the active code was locked out after
                                 MAX_CODE_ATTEMPTS wrong guesses
        "EXPIRED"             -- code existed but is past its expiry
    """
    code_hash = _hash_code(email, raw_code)

    async with pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT lc.id, lc.user_id, lc.code_hash, lc.expires_at, lc.attempts
                   FROM login_codes lc
                   JOIN users u ON u.id = lc.user_id
                   WHERE u.email = $1 AND lc.used_at IS NULL
                   ORDER BY lc.created_at DESC
                   LIMIT 1
                   FOR UPDATE""",
                email,
            )

            if not row:
                return "INVALID_CODE"
            if row["attempts"] >= MAX_CODE_ATTEMPTS:
                return "TOO_MANY_ATTEMPTS"
            if row["expires_at"] < datetime.now(timezone.utc):
                return "EXPIRED"

            if row["code_hash"] != code_hash:
                await conn.execute(
                    "UPDATE login_codes SET attempts = attempts + 1 WHERE id = $1",
                    row["id"],
                )
                return "INVALID_CODE"

            await conn.execute(
                "UPDATE login_codes SET used_at = now() WHERE id = $1", row["id"]
            )

            user_row = await conn.fetchrow(
                "SELECT * FROM users WHERE id = $1 AND active = true", row["user_id"]
            )
            if not user_row:
                return "INVALID_CODE"

            await conn.execute(
                "UPDATE users SET last_login_at = now() WHERE id = $1", user_row["id"]
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