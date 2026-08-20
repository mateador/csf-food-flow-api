# CSF Food Flow API

Backend for the Cambridge Sustainable Food App's Food Flow Log — records
weigh-in and weigh-out events at hubs and the food centre, enforces
role-based access server-side, and produces weekly reports.

## Architecture Overview

- **Framework**: Sanic (async-first Python), see `src/app.py`
- **Database**: PostgreSQL on Neon, raw `asyncpg` (no ORM — see
  `src/db/README.md` for why)
- **Auth**: Passwordless magic-link, httpOnly session cookie (JWT)
- **Contract**: `docs/openapi.yaml` is the canonical API contract, shared
  with the PWA repo — see `docs/CONTRACT.md`

This API serves the PWA exclusively (or primarily). It is not a
general-purpose backend for other consumers in V1.

## Package Structure

```
src/
  app.py                  -- entrypoint: CORS, blueprints, DB lifecycle
  db/
    migrations/0001_init.sql -- schema (all 6 entities)
    migrate.py             -- migration runner (version-table pattern)
    seed.py                -- idempotent seed (categories, locations, admin)
    client.py               -- asyncpg connection pool
  middleware/
    auth.py                 -- session validation, role-requirement decorator
  modules/
    auth/                    -- magic-link request/verify, /me
    entries/                 -- weigh-in/out CRUD, bulk offline sync
    locations/                -- hub/centre CRUD
    categories/                -- food category list
    reports/                   -- weekly totals, CSV export
    users/                      -- admin user management
docs/
  openapi.yaml              -- canonical API contract
  CONTRACT.md                -- contract sync discipline between repos
tests/                        -- (structure present; see Known Gaps below)
```

## Prerequisites

- Python 3.12+
- A Postgres database — either a free [Neon](https://neon.tech) project
  (recommended, ~1 minute to create) or a local Postgres install

## Environment Setup

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# fill in DATABASE_URL, DIRECT_URL, JWT_SECRET (see .env.example comments
# for how to generate a secret), and optionally RESEND_API_KEY
```

## Local Development Commands

```bash
python -m src.db.migrate          # apply schema
python -m src.db.migrate --status # check what's applied vs. pending
python -m src.db.seed             # seed categories, locations, admin user
python -m src.app                 # start the dev server (default :8000)

pytest                            # run tests (see Known Gaps)
```

Without `RESEND_API_KEY` set, magic-link emails print to the console
instead of sending — this is intentional, so local development never needs
a real email provider. Look for the `MAGIC LINK (console fallback)` block
in your terminal after requesting a sign-in link.

## Database Migration and Seed Instructions

See `src/db/README.md` for full detail. Short version:

```bash
python -m src.db.migrate   # idempotent -- safe to run repeatedly
python -m src.db.seed      # idempotent -- safe to run repeatedly
```

Seeds: 3 food categories (FRESH/FROZEN/AMBIENT), one example hub, one
example food centre, and one admin account (`admin@example.org` by
default — override with `SEED_ADMIN_EMAIL`).

## Neon PostgreSQL Connection Notes

You need **two** connection strings from your Neon dashboard:

- `DATABASE_URL` — the **pooled** connection (used by the running app)
- `DIRECT_URL` — the **direct**, non-pooled connection (used only by
  `migrate.py`, since DDL statements behave unreliably through some
  poolers in transaction-pooling mode)

Both are on the same Neon project's "Connection Details" page, usually
labeled "Pooled connection" and "Direct connection" respectively.

## Build Commands

There's no separate build step — Sanic runs directly from source. For
production, `render.yaml` runs `pip install -r requirements.txt` as the
build command.

## Deployment Notes

**API → Render** (`render.yaml` provided):
1. Push this repo to GitHub
2. Render → New → Web Service → connect the repo (reads `render.yaml`
   automatically)
3. Set the env vars `render.yaml` leaves blank: `DATABASE_URL`,
   `DIRECT_URL`, `CORS_ORIGIN` (the PWA's exact deployed URL — never `*`,
   since cookie auth requires an exact origin), `JWT_SECRET`,
   `MAGIC_LINK_BASE_URL`, `RESEND_API_KEY`, `EMAIL_FROM`
4. Render's free tier spins down after 15 minutes idle; the first request
   after that takes 30-50 seconds to wake up. Fine for this app's actual
   usage pattern, worth knowing so it doesn't look broken.
5. After migrating on Render's first deploy (via a one-off shell command
   in the Render dashboard, or by running `python -m src.db.migrate`
   locally against the same Neon database), seed with
   `python -m src.db.seed`.

**Database → Neon**: no separate deployment step — Neon is already live
the moment you create the project. Point `DATABASE_URL`/`DIRECT_URL` in
Render's environment at it.

## Known Assumptions / TODOs

- **A1** — CSV export uses a flat placeholder layout (Date, Location,
  Type, Category, Weight). This is explicitly pending validation against
  CSF's real spreadsheet template, per the original spec. Do not treat
  this layout as final.
- **A2** — Email sending uses Resend's free tier (3,000/month, 100/day)
  with a console-log fallback for local dev.
- **A3** — Session cookie is a JWT (`ACCESS_TOKEN_TTL_MINUTES`, default
  12 hours), not a server-side session store. There is no "sign out
  everywhere" / revoke-all-sessions capability in V1 — the cookie simply
  expires. Worth adding a revocation list if that becomes a real
  requirement.
- **A4** — No automated tests exist yet (`tests/` directory is scaffolded
  but empty). Every endpoint and role rule in this API was verified
  manually against a real Postgres instance during development (see the
  build history for specifics: HUB/FOOD_CENTRE/ADMIN role enforcement,
  Monday-validation, bulk-sync idempotency, and a real role-leak bug in
  the weekly report that was found and fixed this way). Converting that
  manual verification into a `pytest` suite is the highest-value next
  step before this goes further into production use.
- **A5** — Rate limiting on magic-link requests is not yet implemented.
  The spec calls for it; low risk at CSF's actual usage scale, but worth
  adding before wider rollout.
- **A6** — `docs/openapi.yaml` sync between this repo and the PWA repo is
  a manual discipline (see `docs/CONTRACT.md`), not CI-enforced.
