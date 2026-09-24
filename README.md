# CSF Food Flow API

Backend for the Cambridge Sustainable Food App's Food Flow Log — records
weigh-in and weigh-out events at hubs and the food centre, enforces
role-based access server-side, and produces weekly reports.

## Architecture Overview

- **Framework**: Sanic (async-first Python), see `src/app.py`
- **Database**: PostgreSQL on Neon, raw `asyncpg` (no ORM — see
  `src/db/README.md` for why)
- **Auth**: passwordless 4-digit email login code, then an httpOnly
  session cookie (JWT). See "Authentication" below.
- **Hosting**: containerised, running on Azure Container Apps. See
  "Deployment" below.
- **Contract**: `docs/openapi.yaml` is the canonical API contract, shared
  with the PWA repo — see `docs/CONTRACT.md`

This API serves the PWA exclusively. It is not a general-purpose backend
for other consumers in V1.

## Package Structure

```
Dockerfile                -- container image for Azure Container Apps
.dockerignore             -- keeps .env and local files out of the image
.env.example              -- every environment variable the app reads
src/
  app.py                  -- entrypoint: CORS, blueprints, DB lifecycle
  db/
    migrations/           -- schema-as-code, applied in order
      0001_init.sql         -- base schema
      0002_add_name_field.sql
      0003_add_categories.sql
      0004_add_trays.sql    -- tray types, gross/net weight split
      0005_login_codes.sql  -- replaces magic-link tokens
    migrate.py            -- migration runner (version-table pattern)
    seed.py               -- idempotent seed (categories, locations, admin)
    client.py             -- asyncpg connection pool
  middleware/
    auth.py               -- session validation, role-requirement decorator
  modules/
    auth/                 -- login code request/verify, /me
    entries/              -- weigh-in/out CRUD, bulk offline sync
    locations/            -- hub/centre CRUD
    categories/           -- food category list
    tray_types/           -- tray types used for net weight
    reports/              -- weekly totals, CSV export
    users/                -- admin user management
docs/
  openapi.yaml            -- canonical API contract
  CONTRACT.md             -- contract sync discipline between repos
tests/                    -- pytest suite, runs against a real Postgres
pytest.ini                -- pytest configuration
requirements-dev.txt      -- test dependencies (not in the Docker image)
```

## Prerequisites

- Python 3.12+
- A Postgres database — a free [Neon](https://neon.tech) project
  (recommended) or a local Postgres install
- Docker, to run the test database or the container image locally

## Environment Setup

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# fill in DATABASE_URL, DIRECT_URL and JWT_SECRET (see comments in
# .env.example). Leave RESEND_API_KEY empty for local development.
```

## Local Development Commands

```bash
python -m src.db.migrate          # apply schema
python -m src.db.migrate --status # check what's applied vs. pending
python -m src.db.seed             # seed categories, locations, admin user
python -m src.app                 # start the server (default :8000)
```

Without `RESEND_API_KEY` set, login codes print to the console instead of
being emailed, so local development never needs a real email provider.
Look for the `LOGIN CODE (console fallback)` block in your terminal after
requesting a code.

### Running the container locally

```bash
docker build -t csf-api .
docker run --rm -p 8000:8000 --env-file .env csf-api
```

The container listens on port 8000, the same port Azure's ingress targets.

## Running the tests

The suite runs against a real, disposable Postgres database — never Neon.
It drops and recreates that database's schema on every run, so it refuses
to start unless `TEST_DATABASE_URL` is set and the database name contains
`test`.

```bash
# One-off: start a throwaway Postgres on port 5433
docker run -d --name csf-test-db \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=csf_test \
  -p 5433:5432 postgres:16

pip install -r requirements-dev.txt

# TEST_DATABASE_URL can also live in .env (see .env.example)
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/csf_test
pytest
```

Afterwards, `docker stop csf-test-db` stops it and `docker start csf-test-db`
brings it back. No real email is sent during tests.

Tests marked `xfail` describe a known bug: they state the correct
behaviour, and are expected to fail until that bug is fixed. Each one's
`reason` says what's wrong.

## Authentication

Sign-in is a two-step flow on one page of the PWA: the user enters their
email, receives a 4-digit code, and enters it in the same tab. Because a
4-digit code has only 10,000 possible values, the protection comes from
limits rather than from the code itself:

- **Expiry**: a code is valid for `LOGIN_CODE_TTL_MINUTES` (default 10)
  and can be used once.
- **Attempt lockout**: after 5 wrong guesses the active code is locked and
  a new one must be requested.
- **Request throttling**: at most 3 codes per user per 10 minutes, so the
  attempt budget can't be reset by requesting fresh codes.
- **One active code per user**: issuing a new code invalidates the old one.
- **No user enumeration**: the request endpoint returns the same response
  whether or not the email exists, is active, or is throttled.
- Codes are stored hashed (SHA-256, salted with the email), never in
  plaintext.

On success the API sets an httpOnly, `SameSite=Lax` session cookie (a JWT,
`Secure` in production) valid for `ACCESS_TOKEN_TTL_MINUTES` (default 12
hours).

## Database Migration and Seed Instructions

See `src/db/README.md` for full detail. Short version:

```bash
python -m src.db.migrate   # idempotent -- safe to run repeatedly
python -m src.db.seed      # idempotent -- safe to run repeatedly
```

Seeds: six food categories, one example hub, one example food centre, and
one admin account (override the email with `SEED_ADMIN_EMAIL`).

## Neon PostgreSQL Connection Notes

You need **two** connection strings from your Neon dashboard:

- `DATABASE_URL` — the **pooled** connection (used by the running app)
- `DIRECT_URL` — the **direct**, non-pooled connection (used only by
  `migrate.py`, since DDL statements behave unreliably through some
  poolers in transaction-pooling mode)

## Deployment

**API → Azure Container Apps.** The image is built locally from
`Dockerfile`, pushed to Azure Container Registry, and run on Azure
Container Apps.

- **Registry**: `csfacrmateador.azurecr.io`, image `csf-api`
- **Container app**: `csf-api`, resource group `csf-rg`, UK South region
- **Ingress**: external, HTTPS, target port `8000`
- **Scaling**: scales to zero when idle. The first request after an idle
  period waits for a container to start, so expect a short delay. The PWA
  queues entries offline if a request fails, so nothing recorded during a
  cold start is lost.

**Configuration.** The app is configured entirely through container
environment variables (see `.env.example` for the full list).
`DATABASE_URL`, `JWT_SECRET` and `RESEND_API_KEY` are stored as Container
Apps secrets, and their environment variables reference those secrets
rather than holding the values. `DIRECT_URL` is not set on the container,
because only migrations use it and they run locally. No secret is in the
image or the repo.

In production:
- `SANIC_DEV` must be `false` or unset (turns on the `Secure` cookie flag)
- `CORS_ORIGIN` must be the PWA's exact Netlify URL, never `*`

**Releasing a new version.** Images are built and pushed from a local
machine, then the container app is pointed at the new tag. Use a new tag
for every release (`v2`, `v3`, …) so each revision maps to one image.

```bash
docker build -t csfacrmateador.azurecr.io/csf-api:v2 .
docker push csfacrmateador.azurecr.io/csf-api:v2

az containerapp update \
  --name csf-api \
  --resource-group csf-rg \
  --image csfacrmateador.azurecr.io/csf-api:v2
```

The update creates a new revision of the container app running the new
image.

**Migrations** are not run by the container. Run them from a local machine
against the same Neon database (the runner uses `DIRECT_URL`):

```bash
python -m src.db.migrate
python -m src.db.seed      # only needed on a fresh database
```

**How the PWA reaches the API.** The PWA never calls this app's Azure URL
from the browser. Netlify proxies `/api/*` on the PWA's own origin to the
container app server-side (see the PWA's `netlify.toml`), which keeps the
session cookie first-party in Safari and private browsing.

**Database → Neon**: no separate deployment step. Point
`DATABASE_URL`/`DIRECT_URL` at the Neon project.

## Known Assumptions / TODOs

- **A1** — CSV export uses a flat placeholder layout. It is pending
  validation against CSF's real spreadsheet template. Do not treat this
  layout as final.
- **A2** — Email sending uses Resend's free tier (3,000/month, 100/day)
  with a console-log fallback for local dev. A Resend failure is logged at
  error level and never breaks the code-request endpoint.
- **A3** — The session cookie is a JWT, not a server-side session store.
  There is no "sign out everywhere" capability in V1 — the cookie simply
  expires. It also means deactivating a user doesn't end a session they
  already have; it lasts until the cookie expires
  (`ACCESS_TOKEN_TTL_MINUTES`). `tests/test_auth.py` records this as an
  expected failure.
- **A4** — Automated tests cover auth, entries, bulk sync, reports and
  admin endpoints against a real Postgres database (see "Running the
  tests"). Tests run locally; there is no CI pipeline yet. Known bugs are
  recorded as `xfail` tests rather than left undocumented.
- **A5** — `docs/openapi.yaml` sync between this repo and the PWA repo is
  a manual discipline (see `docs/CONTRACT.md`), not CI-enforced.