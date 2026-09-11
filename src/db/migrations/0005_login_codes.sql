-- Migration 0005: replace magic-link tokens with 4-digit login codes.
--
-- Tokens/codes are inherently ephemeral -- nothing in magic_link_tokens is
-- worth preserving (every row is either already used, already expired, or
-- about to be). Dropping and recreating under a clearer name is simpler
-- and less error-prone than repurposing columns in place.
--
-- Two things this table has that the old one didn't, both existing
-- specifically because a 4-digit code (10,000 possible values) needs
-- real brute-force protection that a long random token never did:
--   - attempts: incremented on every wrong guess, checked against a
--     MAX_CODE_ATTEMPTS limit in application code before a code is
--     accepted at all.
--   - (rate limiting on code REQUESTS, not stored here, is done in
--     application code by counting recent rows per user_id.)

DROP TABLE IF EXISTS magic_link_tokens;

CREATE TABLE login_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    code_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT login_codes_hash_unique UNIQUE (code_hash)
);

CREATE UNIQUE INDEX idx_login_codes_hash ON login_codes (code_hash);
CREATE INDEX idx_login_codes_user_id ON login_codes (user_id);
CREATE INDEX idx_login_codes_expires_at ON login_codes (expires_at);
CREATE INDEX idx_login_codes_active ON login_codes (used_at) WHERE used_at IS NULL;
-- Supports the rate-limit query (count recent codes for this user) and
-- the verify lookup (most recent active code for this user) together.
CREATE INDEX idx_login_codes_user_created ON login_codes (user_id, created_at DESC);