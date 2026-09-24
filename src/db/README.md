# Database — `src/db/`

PostgreSQL on Neon, schema-as-code, no ORM (raw `asyncpg` — see
`docs/CONTRACT.md` and the earlier shopping-list-api build for the
reasoning: this schema is small and relationship-light enough that an
ORM's abstraction cost isn't worth paying, and the app's actual query
patterns favor transparent, hand-written SQL).

## Files

- `migrations/0001_init.sql` — the base schema. All 6 original entities
  (`locations`, `users`, `food_categories`, `weigh_entries`, `audit_log`,
  `magic_link_tokens`), every index and constraint from the spec.
- `migrations/0002`–`0005` — later changes, applied in order: the
  mandatory `name` field, three extra food categories, tray types with
  the gross/net weight split, and `login_codes` replacing
  `magic_link_tokens`.
- `migrate.py` — runner. Tracks applied migrations in a `schema_migrations`
  table (version + timestamp). Not Alembic, deliberately — see the root
  README for why.
- `seed.py` — idempotent. Seeds the 3 food categories, one example hub +
  one food centre, and one admin user. Safe to re-run.
- `client.py` — the `asyncpg` connection pool, initialized once at app
  startup.

## Local setup

```bash
# 1. Set DATABASE_URL and DIRECT_URL in .env (see .env.example)

# 2. Apply the schema
python -m src.db.migrate

# 3. Seed reference data + an admin account
python -m src.db.seed
```

Check migration status without applying anything:
```bash
python -m src.db.migrate --status
```

## Why two connection strings

`DIRECT_URL` (non-pooled) is used only by `migrate.py`. `DATABASE_URL`
(pooled, via Neon's connection pooler) is used everywhere else. Some
poolers handle DDL statements (`CREATE TABLE`, `ALTER TABLE`) unreliably in
transaction-pooling mode — migrations connect directly to sidestep that
entirely, rather than debug an intermittent migration failure later.

## Verified, not just written

Every constraint in `0001_init.sql` was tested directly against a real
Postgres instance before this was considered done:
- HUB role without `location_id` → correctly rejected
- IN entry with a `destination_location_id` → correctly rejected
- OUT entry with no `destination_location_id` → correctly rejected
- OUT entry with `destination_location_id == location_id` → correctly rejected
- Negative `weight_kg` → correctly rejected
- Duplicate `client_uuid` → correctly rejected (this is what makes offline
  bulk sync idempotent)

The migration runner and seed script were both run twice in a row to
confirm idempotency (no duplicate rows, no errors on re-run).
