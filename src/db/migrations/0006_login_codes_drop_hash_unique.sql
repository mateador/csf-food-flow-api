-- Migration 0006: allow the same code hash to appear more than once.
--
-- code_hash is sha256(email:code), and a code has only 10,000 possible
-- values. So a user who signs in regularly will eventually be issued a code
-- value they've had before, which produces the same hash as an old, used
-- row. 0005 made code_hash UNIQUE, so that insert failed and the request
-- endpoint returned 500.
--
-- Nothing looks a code up by its hash: verification finds the user's most
-- recent unused code (idx_login_codes_user_created) and compares hashes in
-- application code. So the uniqueness guarantee protected nothing, and no
-- replacement index is needed.

ALTER TABLE login_codes DROP CONSTRAINT IF EXISTS login_codes_hash_unique;
DROP INDEX IF EXISTS idx_login_codes_hash;