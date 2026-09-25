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
.github/workflows/
  ci-deploy.yml           -- tests on every push/PR; deploys main to Azure
  uptime.yml              -- checks the service in hub hours; emails on failure
  backup.yml              -- nightly verified backup to Azure Blob Storage
scripts/
  verify-backup.sh        -- proves a restored backup matches production
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
- **Old codes are deleted**: whenever a code is issued, every code (for any
  user) that expired more than 24 hours ago is removed, so the table
  doesn't grow forever. The throttle only looks back 10 minutes, so this
  never weakens it.
- **No user enumeration**: the request endpoint returns the same response
  whether or not the email exists, is active, or is throttled.
- Codes are stored hashed (SHA-256, salted with the email), never in
  plaintext.

On success the API sets an httpOnly, `SameSite=Lax` session cookie (a JWT,
`Secure` in production) valid for `ACCESS_TOKEN_TTL_MINUTES` (default 12
hours).

Signing out is `POST /auth/logout`, which expires the cookie on that
device. The browser can't delete an httpOnly cookie itself, so without
this call a shared device would stay signed in as the previous person.

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

**Releasing a new version.** Pushing to `main` releases automatically,
through `.github/workflows/ci-deploy.yml`:

1. The test suite runs against a Postgres service container, and the
   image is built once as a check.
2. If that passes, the image is built and pushed to the registry, tagged
   `git-<first 7 characters of the commit>`, so every image is unique and
   traceable to its commit.
3. The container app is updated to that image, creating a new revision.
4. The new revision's own address is polled on `/api/v1/health` for up to
   five minutes. Until the new revision is ready, the app keeps serving
   the previous one.

The run's summary shows the image it deployed and the rollback command
for the image that was running before. Pull requests run the tests only.
The workflow signs in to Azure with OpenID Connect, so no Azure password
is stored in GitHub. The three repository secrets are `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`.

To redeploy without a new commit, run the workflow from the Actions tab
("Run workflow" on `main`).

**Rolling back:**

```bash
az acr repository show-tags --name csfacrmateador --repository csf-api \
  --orderby time_desc -o table
az containerapp update --name csf-api --resource-group csf-rg \
  --image csfacrmateador.azurecr.io/csf-api:<previous tag>
```

**Releasing by hand** is still possible if GitHub Actions is unavailable:
`az acr login --name csfacrmateador`, then `docker build`, `docker push`
and `az containerapp update` with a tag that has never been used. Only run
the update after the push has succeeded.

**Migrations** are not run by the container or by the workflow. Run them
from a local machine against the same Neon database (the runner uses
`DIRECT_URL`), **before** pushing code that depends on them. Pushing to
`main` deploys straight away, so code that expects a new column must not
reach `main` until the migration has been applied:

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

## Monitoring

Two health endpoints:

- `GET /api/v1/health` — **liveness**: the process is up. Touches nothing
  else, so it stays cheap and never wakes the database.
- `GET /api/v1/health/ready` — **readiness**: the API can reach the
  database. It returns 503 if it can't. Used by the uptime check and the
  deploy check. It wakes the database, so the PWA never calls it.

`.github/workflows/uptime.yml` calls readiness every two hours in hub
hours, Monday to Saturday, both directly and through the Netlify proxy
(repository variable `PWA_URL`). A failed run emails whoever last edited
its schedule. The interval is deliberate: each check can wake the
scaled-to-zero app for about five minutes, and the free Azure allowance
is about 100 hours awake a month at this app's size.

## Backups and restore

Neon's Free plan only restores to a point in the last 6 hours, so there's
an independent nightly backup, `.github/workflows/backup.yml`:

1. `pg_dump` runs with a **read-only** database role
   (`BACKUP_DATABASE_URL`), on the same Postgres major version as the
   server.
2. The dump is **restored into a throwaway database, and every table's
   row count is checked against production**
   (`scripts/verify-backup.sh`). A backup that can't be restored fails
   the run.
3. It's uploaded to a private container, `db-backups`, in an Azure
   Storage account in UK South (repository variable
   `BACKUP_STORAGE_ACCOUNT`). Backups are kept 90 days, and deletions are
   recoverable for 7 days.

Backups never pass through GitHub: no artifacts, and nothing in the logs.

**Restoring.** Practise this once, before it's needed.

```bash
SA=<storage account name>

# 1. Find and download a backup (names sort by date)
az storage blob list --account-name "$SA" --container-name db-backups \
  --auth-mode key --query "[].name" -o tsv | sort | tail -5
az storage blob download --account-name "$SA" --container-name db-backups \
  --auth-mode key --name <backup name> --file restore.dump

# 2. Inspect it in a throwaway local database first
docker run -d --name csf-restore -e POSTGRES_PASSWORD=restore -p 5434:5432 postgres:<server major>
docker exec -i csf-restore pg_restore -U postgres -d postgres --no-owner --no-privileges < restore.dump
psql postgresql://postgres:restore@localhost:5434/postgres -c "SELECT count(*) FROM weigh_entries"
```

**To recover production:**
1. In the Neon console, create a new, empty database on the main branch
   (Databases → New database).
2. Restore into it using its **direct** connection string:
   `pg_restore --no-owner --no-privileges -d "<direct URL>" restore.dump`
3. Point the app at the new database, then restart the revision so it
   picks up the changed secret:
   `az containerapp secret set --name csf-api --resource-group csf-rg --secrets database-url="<pooled URL>"`
   `az containerapp revision restart --name csf-api --resource-group csf-rg --revision <latest revision>`
4. Update `DIRECT_URL` in your local `.env` and `BACKUP_DATABASE_URL` in
   GitHub to the new database.
   
## Known Assumptions / TODOs

- **A1** — CSV export uses a flat placeholder layout. It is pending
  validation against CSF's real spreadsheet template. Do not treat this
  layout as final.
- **A2** — Email sending uses Resend's free tier (3,000/month, 100/day)
  with a console-log fallback for local dev. A Resend failure is logged at
  error level and never breaks the code-request endpoint.
- **A3** — The session cookie is a JWT, but it only proves identity. On
  every request the API reads the user's current status, role and hub
  from the database, so deactivating a user, changing their role or
  moving them to another hub takes effect on their next request. There
  is still no "sign out everywhere" button separate from deactivation.
- **A4** — Automated tests cover auth, entries, bulk sync, reports and
  admin endpoints against a real Postgres database (see "Running the
  tests"). They run on every push and pull request in GitHub Actions, and
  a deploy only happens when they pass. Known bugs are recorded as `xfail`
  tests rather than left undocumented.
- **A5** — `docs/openapi.yaml` sync between this repo and the PWA repo is
  a manual discipline (see `docs/CONTRACT.md`). CI compares the two copies
  on every run and warns when they differ, but doesn't block a deploy,
  because one repo is always updated before the other.