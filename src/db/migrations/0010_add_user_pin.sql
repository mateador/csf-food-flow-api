-- Migration 0010: 4-digit PIN login, replacing the emailed one-time code
-- as the PWA's default sign-in flow.
--
-- The emailed-code system (login_codes, src/modules/auth/email.py) is
-- deliberately left fully intact and still reachable -- per explicit
-- instruction it stays dormant rather than removed, so re-enabling
-- proper emailed-code login later is a frontend change, not a rebuild.
--
-- pin_hash is nullable: NULL means "hasn't chosen a PIN yet", which is
-- exactly the signal the API uses to offer first-time PIN setup instead
-- of a login form. New users created after this migration start at NULL
-- and go through that flow the first time they sign in -- no special
-- casing needed anywhere else.
--
-- Existing users are backfilled to a shared, known PIN (1717) so nobody
-- is locked out the moment this ships. Computed in SQL via pgcrypto's
-- digest() (already enabled -- see migration 0001), matching the same
-- sha256(email:pin) shape application code uses for login-code hashing.
-- This is a deliberately weak starting point (everyone shares the same
-- guessable PIN); closing that gap is the "Change my PIN" follow-up,
-- not this migration.
ALTER TABLE users ADD COLUMN pin_hash TEXT;

UPDATE users
SET pin_hash = encode(digest(lower(email) || ':1717', 'sha256'), 'hex')
WHERE pin_hash IS NULL;
