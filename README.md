# LJM Intelligence

Broker intelligence, lead finder, inbox analysis, load-board monitoring and
outreach engine for a freight carrier. One dashboard, one API, one background
worker, real data.

- [1. Architecture](#1-architecture)
- [2. Run locally with Docker](#2-run-locally-with-docker)
- [3. Services to deploy](#3-services-to-deploy)
- [4. Environment variables](#4-environment-variables)
- [5. Configured in the app (encrypted vault)](#5-configured-in-the-app-encrypted-vault-not-env)
- [6. Gmail / Google Workspace setup](#6-gmail--google-workspace-setup)
- [7. Scheduled jobs](#7-scheduled-jobs)
- [8. Database and migrations](#8-database-and-migrations)
- [9. Security notes](#9-security-notes)
- [10. Troubleshooting](#10-troubleshooting)
- [11. Development](#11-development)

## 1. Architecture

```
 Browser
    |  HTTPS (ljm_session HttpOnly cookie)
    v
 frontend  (Next.js 16, BFF)  ---- /api/auth/login, /api/proxy/[...path]
    |  server-side fetch, attaches  Authorization: Bearer <jwt>
    v
 api  (FastAPI, :8000)  <---- X-Cron-Secret ---- scheduler (GitHub Actions or any cron)
    |            |
    |            +--> agent-browser (Playwright/Chromium sidecar, :8080)  [optional]
    |            +--> external: Gmail API, Google Admin SDK, FMCSA, Gemini, Claude, load boards, EIA
    v
 postgres 16  <---- worker (Procrastinate, drains the `default` queue)
```

| Component | Folder | Stack | Role |
|---|---|---|---|
| frontend | `frontend/` | Next.js 16, React 19, Tailwind, shadcn | Server-first UI. Also a BFF: login route + same-origin proxy that holds the session cookie, so the browser never sees a bearer token. |
| api | `backend/` | FastAPI, SQLAlchemy 2 async, Alembic | All business logic, auth, cron-callable endpoints. Runs migrations on boot. |
| worker | `backend/` (`Dockerfile.worker`) | Procrastinate | Drains the Postgres-backed job queue (mail ingest, enrichment, AI analysis, rate refresh). |
| postgres | – | PostgreSQL 16 | System of record, job queue (Procrastinate tables), encrypted credential vault. |
| agent-browser | `agent-browser/` | FastAPI + Playwright/Chromium | Headless-browser sidecar used by the load-board / broker-page agents. Optional. |

The backend is a tenant-ready modular monolith (`app/identity`, `integrations`,
`prospecting`, `outreach`, `inbox`, `analysis`, `shared`, plus `rates`,
`vetting`, `followups`, `fleet`). Module boundaries are enforced by
import-linter (`backend/.importlinter`). Deeper design notes:
[`docs/architecture.md`](./docs/architecture.md),
[`backend/docs/architecture.md`](./backend/docs/architecture.md),
[`frontend/README.md`](./frontend/README.md).

## 2. Run locally with Docker

Prerequisites: Docker with the Compose plugin.

1. Create an env file beside the compose files (it is git-ignored; `.env*` is in `.gitignore`):

   ```bash
   cat > .env.local-docker <<'EOF'
   APP_ENV=development
   CRON_SECRET=local-dev-cron-secret
   # AI keys are optional for a first boot in development:
   # GEMINI_API_KEY=
   # ANTHROPIC_API_KEY=
   # Optional, enables the agent-browser sidecar auth (see section 9):
   # AGENT_BROWSER_TOKEN=
   EOF
   ```

   `APP_ENV=production` refuses to boot without `GEMINI_API_KEY`; use `development` locally.

2. Start the whole stack (postgres, api, worker, agent-browser, frontend):

   ```bash
   COMPOSE_PROJECT_NAME=ljm-local docker compose \
     -f docker-compose.yml -f docker-compose.local.yml \
     --env-file .env.local-docker up -d --build
   ```

   `docker-compose.local.yml` is a local override: host ports, the `frontend`
   service (runs `next dev`), and `alembic upgrade head` before uvicorn.

3. Ports

   | Service | URL / port |
   |---|---|
   | frontend | http://localhost:3001 |
   | api | http://localhost:8001 (`/health`) |
   | agent-browser | http://localhost:8081 (`/health`) |
   | postgres | localhost:5460 (user/db/password `ljm`) |

4. First login. Migrations seed a demo owner account on an empty database (see
   `backend/migrations/versions/0023_owner_account_hardcoded.py` for the
   credentials). Sign in at http://localhost:3001 and change the password for
   any shared environment.

5. Logs and status

   ```bash
   export COMPOSE_PROJECT_NAME=ljm-local
   C="docker compose -f docker-compose.yml -f docker-compose.local.yml --env-file .env.local-docker"
   $C ps
   $C logs -f api worker
   curl -s localhost:8001/health      # {"ok":true,"db":"up",...}
   ```

6. Reset data (wipes the Postgres volume; migrations and seed re-run on next start):

   ```bash
   $C down -v
   $C up -d --build
   ```

   `$C down` (without `-v`) stops containers and keeps data.

Without the local override, `docker compose up -d` runs the base
`docker-compose.yml` (postgres, api, worker, agent-browser; no frontend),
reading `.env` beside it. Ports there: api `${API_PORT:-8000}`, postgres
`${POSTGRES_PORT:-5432}`, agent-browser `${AGENT_BROWSER_PORT:-8080}`.

## 3. Services to deploy

Any container host (or plain Docker Compose on a VM) works. All services are
stateless except Postgres.

| Service | Build | Port | Health check | Notes |
|---|---|---|---|---|
| api | `backend/Dockerfile`, context `backend/` | 8000 (`$PORT`) | `GET /health` -> `{"ok":true,"db":"up"}` (`?include=jobs` adds queue stats) | Runs `alembic upgrade head` then uvicorn on every start; a failed migration fails the deploy. Runs as non-root. Behind a proxy, `--proxy-headers` is on. Scale horizontally; migrations are idempotent. |
| worker | `backend/Dockerfile.worker`, context `backend/` | none | process liveness | Same code + env as api, but `DATABASE_URL` must be the **direct** (non-pooled) Postgres URL. Scale with `docker compose up -d --scale worker=N`. Required for queue-backed features unless the drain cron (section 7) is used instead. |
| frontend | `frontend/Dockerfile`, context `frontend/` | 3000 | `GET /` (redirects to login) | The shipped Dockerfile runs `pnpm dev`. For production, build with `pnpm build` and run `pnpm start` (change the `CMD`), or deploy with any Next.js-capable host with root directory `frontend`. |
| agent-browser | `agent-browser/Dockerfile`, context `agent-browser/` | 8080 (`$PORT`) | `GET /health` (open, no token needed) | Chromium: give it a plan with at least ~1 GB RAM (smaller instances get OOM-killed). One sidecar can serve all sources. Optional: without `AGENT_BROWSER_URL` the agent sources are simply disabled. |
| postgres | `postgres:16` | 5432 | `pg_isready` | Managed Postgres 16 is fine. Needs a direct connection for the worker (LISTEN/NOTIFY), and TLS (`sslmode=require`) when remote. |

Minimum viable deployment: postgres + api + worker + frontend. Add agent-browser
only for the load-board / broker-page agents.

## 4. Environment variables

Secrets are marked **(secret)**: store them in the host's secret manager, never
in git. Backend settings are read by `backend/app/config.py` (api and worker
share them).

### api + worker

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | – | `postgresql://user:pass@host:5432/db` (scheme is rewritten to psycopg; keep `?sslmode=` params). May be a pooled URL for the api. **(secret)** |
| `DATABASE_URL_DIRECT` | recommended | unset | Direct, non-pooled URL. The worker must use it (LISTEN/NOTIFY, session-scoped `SET LOCAL`). With a pooler in front, set api `DATABASE_URL` = pooled and worker `DATABASE_URL` = direct. Without a pooler both are the same URL. **(secret)** |
| `APP_ENV` | no | `development` | `development` / `test` / `production`. `production` closes `/docs` + `/openapi.json`, drops localhost CORS origins, and refuses to boot without `GEMINI_API_KEY`. |
| `LOG_LEVEL` | no | `info` | `debug`/`info`/`warning`/`error`. |
| `DB_POOL_SIZE` / `DB_POOL_OVERFLOW` | no | `3` / `2` | Per-process pool; size against Postgres `max_connections`. |
| `DB_HEALTH_TIMEOUT` | no | `8` | Seconds before `/health` reports `db:"down"`. |
| `CRON_SECRET` | yes in production | unset | Shared secret for every scheduled call (`X-Cron-Secret` header). **If unset, cron routes are unauthenticated** and a warning is logged. Generate: `openssl rand -hex 32`. **(secret)** |
| `TENANT_CRED_KEY` | no | auto-bootstrapped | Credential-vault key: base64 of exactly 32 bytes (`openssl rand -base64 32`). Resolution order: env, then a key persisted in the database settings row, then generated on first use. Set it explicitly to keep the key outside the database, and set the **same** value on api and worker. Losing it makes stored credentials undecryptable. **(secret)** |
| `AUTH_JWT_SECRET` | no | generated by migration, stored in DB | Optional override for the JWT signing secret. **(secret)** |
| `AUTH_ACCESS_TTL_DAYS` | no | `7` | Session lifetime (1-30). |
| `CORS_ALLOWED_ORIGINS` | prod | `http://localhost:3000` | Comma-separated browser origins (the frontend URL). |
| `CORS_ALLOWED_ORIGIN_REGEX` | no | unset | For preview URLs. |
| `FRONTEND_ORIGIN` | recommended | unset | Canonical frontend URL; used to build links. |
| `TRUSTED_PROXY_SECRET` | recommended | unset | Must equal the frontend's `LJM_PROXY_SECRET`; lets the API trust the forwarded client IP for per-IP login rate limiting. **(secret)** |
| `TRUSTED_PROXY_HOPS` | no | `1` | Proxy hops to skip when reading `X-Forwarded-For`. |
| `JOBS_IN_PROCESS_KICK_ENABLED` / `_SECONDS` | no | `true` / `10` | API opportunistically drains a few jobs after enqueue. Harmless alongside a worker. |
| `SENTRY_DSN` / `SENTRY_TRACES_SAMPLE_RATE` | no | unset / `0.0` | Unset = Sentry not initialised. |

**AI**

| Variable | Required | Default |
|---|---|---|
| `GEMINI_API_KEY` | yes when `APP_ENV=production` (paid tier required) | unset **(secret)** |
| `GEMINI_MODEL`, `GEMINI_MODEL_SCORING`, `GEMINI_MODEL_DISCOVERY`, `GEMINI_MODEL_ENRICHMENT` | no | `gemini-3.5-flash-lite` |
| `ANTHROPIC_API_KEY` | no | unset **(secret)** |
| `CLAUDE_DEFAULT_MODEL` / `CLAUDE_CHEAPER_MODEL` | no | `claude-sonnet-5-5` / `claude-haiku-4-5-20251001` |

**FMCSA crawl and enrichment**

| Variable | Default | Notes |
|---|---|---|
| `FMCSA_APP_TOKEN` | unset | FMCSA QCMobile web key **(secret)**. |
| `FMCSA_PAGE_SIZE` | `500` | Records per page. |
| `FMCSA_PER_RUN_PAGE_CAP` | `6` | Pages per scheduled run. |
| `FMCSA_BACKFILL_PAGE_CAP` | `40` | Pages per backfill run. |
| `FMCSA_TIME_BUDGET_S` | `120` | Wall-clock cap per run. |
| `OSM_OVERPASS_ENABLED` / `OSM_OVERPASS_MAX_STATES` / `OSM_OVERPASS_STATES` | `true` / `0` (no cap) / empty | Warehouse discovery via OpenStreetMap; restrict by 2-letter state codes. |
| `ENRICHMENT_ENABLED`, `ENRICHMENT_PER_RUN_CAP` (10), `ENRICHMENT_PAGE_CAP_PER_COMPANY` (8), `ENRICHMENT_REQUEST_TIMEOUT_S` (15), `ENRICHMENT_BETWEEN_REQUESTS_S` (1.5), `ENRICHMENT_CACHE_TTL_HOURS` (24) | see left | Contact-enrichment throttles. |
| `DISCOVERY_PER_RUN_CAP`, `DISCOVERY_INDUSTRIES` | `8`, built-in list | AI shipper discovery per run. |

**Mail (sending stays off unless explicitly enabled)**

| Variable | Default | Notes |
|---|---|---|
| `MAILBOX_SOURCE` | `simulated` | `gmail` reads real mailboxes. Also switchable from Settings. |
| `MAIL_SENDER` | `simulated` | `gmail` enables real sending paths. |
| `MAIL_OWNER_SEND_ENABLED` | `false` | Master switch for owner-initiated sends. |
| `SIMULATED_DELIVERY` | `true` | Outreach demo safety net. Real outreach requires this `false` **and** a real sender. |
| `GMAIL_SA_JSON` | unset | Service-account JSON. Prefer the Settings UI (section 5). **(secret)** |
| `GMAIL_IMPERSONATE` | built-in sender mailbox | Mailbox used for sends. |
| `GMAIL_ADMIN_IMPERSONATE` | empty | Workspace admin used to list all mailboxes (Directory API). |
| `GMAIL_SCOPES_SEND` / `_READ` / `_ADMIN` | send / readonly / `admin.directory.user.readonly` | Rarely changed. |
| `GMAIL_PER_MAILBOX_RPS` / `GMAIL_GLOBAL_RPS` / `GMAIL_BACKOFF_MAX_SECONDS` | `2` / `20` / `60` | Rate limiting and backoff. |
| `OUTREACH_FROM_EMAIL`, `OUTREACH_FROM_NAME` | built-in | Sender identity. |
| `OUTREACH_POSTAL_ADDRESS` | empty | CAN-SPAM footer address; required for real sends. |
| `UNSUBSCRIBE_BASE_URL` | built-in | Public API base URL used in unsubscribe links; set to your API's public URL. |
| `UNSUBSCRIBE_SECRET` | unset | HMAC secret for unsubscribe tokens **(secret)**. |

**Load boards** (env-gated; each board is on only when all its vars are set. Credentials can instead be entered in Settings, section 5)

| Board | Variables |
|---|---|
| DAT | `DAT_SERVICE_ACCOUNT_EMAIL`, `DAT_SERVICE_ACCOUNT_PASSWORD`, `DAT_ORG_ID` (+ `DAT_BASE_URL`, `DAT_RPS`) |
| C.H. Robinson | `CHR_CLIENT_ID`, `CHR_CLIENT_SECRET`, `CHR_CARRIER_CODE` (+ `CHR_SCOPE`, `CHR_RPS`) |
| 123Loadboard | `LB123_API_KEY`, `LB123_CARRIER_USERNAME`, `LB123_CARRIER_PASSWORD` |
| Truckstop | `TRUCKSTOP_INTEGRATION_ID`, `TRUCKSTOP_USERNAME`, `TRUCKSTOP_PASSWORD` |
| Driver mode per source | `LOADS_DAT_DRIVER`, `LOADS_CHR_DRIVER`, `LOADS_LB123_DRIVER`, `LOADS_TRUCKSTOP_DRIVER` = `off` (default) / `api` / `agent`; `LOADS_AGENT_KILL` = comma list of sources to hard-disable |

**Agent loop** (api side): `AGENT_BROWSER_URL` (empty = disabled), `AGENT_BROWSER_TOKEN`,
`AGENT_DAILY_RUNS_PER_SOURCE` (48), `AGENT_MAX_STEPS` (20),
`AGENT_MAX_INPUT_TOKENS` (120000), `AGENT_MAX_OUTPUT_TOKENS` (4000). URL and
token can also be set in Settings (section 5).

### frontend

| Variable | Required | Notes |
|---|---|---|
| `API_URL` | yes | Server-only base URL of the API (the BFF/proxy upstream), e.g. `http://api:8000`. Falls back to `NEXT_PUBLIC_API_URL`. |
| `NEXT_PUBLIC_API_URL` | yes | Browser-visible API origin, used for link building and the typed client. Baked at build time. |
| `LJM_PROXY_SECRET` | recommended | Server-only. Forwarded to the API so per-IP login rate limiting sees the real client IP. Must equal api `TRUSTED_PROXY_SECRET`. **(secret)** |
| `BACKEND_URL` | legacy | Used by remaining legacy `/api/*` handlers (docker local sets it to `http://api:8000`). |
| `CRON_SECRET` | legacy | Only for legacy proxy handlers that trigger crawl/email tools; same value as api. **(secret)** |

### agent-browser

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8080` | Listen port. |
| `AGENT_BROWSER_TOKEN` | empty | Shared secret; must match the value the API uses. Without it the service refuses requests (section 9). Generate: `openssl rand -hex 24`. **(secret)** |
| `AGENT_BROWSER_IDLE_SECONDS` | `600` | Idle browser-context reaping. |

### docker-compose (base file)

`POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` (default `ljm`; change the
password for any non-local use), `POSTGRES_PORT`, `API_PORT`,
`AGENT_BROWSER_PORT`, plus everything above via `.env`.

## 5. Configured in the app (encrypted vault, not env)

An owner signs in and uses **Settings**. These values are encrypted with the
vault key (`TENANT_CRED_KEY`, or the auto-bootstrapped key) and stored in
Postgres, so no environment change or restart is needed:

- **Gmail connection**: service-account JSON, impersonated mailbox, and the two
  switches **Inbox source** (simulated/gmail) and **Send enabled**.
  The API process caches the credentials after saving and primes the cache from
  the vault on boot.
- **Load-board logins**: DAT, Truckstop, 123Loadboard, C.H. Robinson
  credentials, per-source driver (off/api/agent), and **broker page URLs**
  (the pages the broker-page agent reads).
- **Agent-browser**: sidecar URL and token, with a connection test.
- **EIA API key** for weekly diesel prices.
- Outreach toggles such as auto-outreach (off by default).

Precedence is env first, then the vault/settings row, then the built-in default.
The app works with zero connector env vars set.

## 6. Gmail / Google Workspace setup

Reading (and optionally sending) uses a Google service account with
domain-wide delegation, so no per-user OAuth consent is needed.

1. In Google Cloud, create or pick a project.
2. Enable the **Gmail API** and the **Admin SDK API**.
3. Create a **service account**, then create a **JSON key** for it. Keep the
   file secret.
4. In Google Workspace Admin -> Security -> API controls -> **Domain-wide
   delegation**, add the service account's numeric Client ID with exactly these
   scopes:
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/gmail.send
   https://www.googleapis.com/auth/admin.directory.user.readonly
   ```
   (`gmail.send` is only exercised when sending is enabled.)
5. In the app: Settings -> Gmail -> paste the JSON key, set the impersonated
   mailbox, save. Alternatively set `GMAIL_SA_JSON` + `GMAIL_IMPERSONATE` in env.
6. Choose how mailboxes are discovered:
   - **All mailboxes**: set `GMAIL_ADMIN_IMPERSONATE` to a Workspace admin
     address; the Directory API lists every user.
   - **Explicit list**: no admin needed; pass `mailboxes` in the backfill call.
7. Switch **Inbox source** to `gmail` (Settings, or `MAILBOX_SOURCE=gmail`).
8. Check `GET /mail/status` (authenticated): `sa_configured`, `mailbox_source`,
   `read_mailboxes_count`, `reason`.

**Backfill** (user bearer or `X-Cron-Secret`):

```bash
curl -X POST "$API/mail/backfill" \
  -H "X-Cron-Secret: $CRON_SECRET" -H "Content-Type: application/json" \
  -d '{"mailboxes":["user@example.com"],"hours":2}'
```

Body fields: `mailbox` (single), `mailboxes` (explicit list; ignored if
`mailbox` set), and a window: `hours` (1-720, wins) > `days` (1-3650) >
`months` (default 12). Omit mailbox fields to fan out across all discovered
mailboxes. `POST /mail/incremental` syncs new mail since the last history id.
Ingest is queued and processed by the worker; poll `GET /jobs/{id}`.

**Sending is off by default.** Real sends need `Send enabled` on (Settings or
`MAIL_OWNER_SEND_ENABLED=true`), `MAIL_SENDER=gmail`, `SIMULATED_DELIVERY=false`
for outreach, and `OUTREACH_POSTAL_ADDRESS` set. Leave all off for read-only.

## 7. Scheduled jobs

All jobs are plain HTTP calls authenticated by `X-Cron-Secret: $CRON_SECRET`.
Any scheduler works (cron + curl, Kubernetes CronJob, cloud scheduler).
GitHub Actions workflows are provided in `.github/workflows/`; set repo secrets
`BACKEND_URL` (API base URL, no trailing slash) and `CRON_SECRET`.

| Job | Cadence | Call | Workflow |
|---|---|---|---|
| Daily crawl (FMCSA, OSM, enrichment) | daily 15:00 America/New_York (workflow fires at 19:00 and 20:00 UTC to cover DST; the backend dedupes) | `POST /crawl/run?trigger=cron` | `daily-crawl.yml` |
| Auto-send after crawl | right after the crawl, same run | `POST /enrichment/auto-send` body `{"dry_run": false}`. Returns 200 with a status (`disabled`, `no_footer`, `no_template`, `outside_window`, `no_unsub_config` are config states, not failures); a no-op unless auto-outreach is enabled in Settings | `daily-crawl.yml` |
| Load-board agents | hourly, per source (`dat`, `truckstop`, `loadboard123`, `chr`, `broker_page`) | `POST /loads/sources/{src}/refresh` (session blob: `GET`/`POST /loads/sources/{src}/session`) | `loads-agent.yml` |
| Queue drain | every 5 min | `POST /jobs/drain?seconds=25` | `queue-drain.yml` |

Notes:

- **Queue drain is only needed when no worker runs.** With the `worker`
  service running continuously, `/jobs/drain` is a safe no-op; you can delete
  `queue-drain.yml`.
- The loads workflow starts its own Playwright sidecar inside the CI job; with
  a deployed agent-browser you can instead run the same `refresh` calls from any
  scheduler and point the app at the sidecar (section 5).
- Example crawl from cron:
  `curl -fsS -X POST "$API/crawl/run?trigger=cron" -H "X-Cron-Secret: $CRON_SECRET"`.
- A crawl is idempotent per day (MC/DOT/domain unique indexes), so retries are safe.

## 8. Database and migrations

- PostgreSQL **16**. Migrations are Alembic (`backend/migrations/versions`).
- **Migrations run automatically on api start** (`alembic upgrade head` in the
  image `CMD`). The worker does not migrate; start/restart it after the api has
  applied new migrations. Manual run:
  `docker compose run --rm api alembic upgrade head`.
- Row-level security with per-row `tenant_id` is in place; the schema is
  tenant-ready.
- The queue lives in the same database (Procrastinate tables); there is no
  separate broker.
- **Backups**: use `pg_dump` (or the managed provider's PITR). Example:
  `docker compose exec postgres pg_dump -U ljm -Fc ljm > ljm.dump`; restore with
  `pg_restore -d ljm --clean ljm.dump`. Back up the vault key
  (`TENANT_CRED_KEY`) separately if you set it explicitly; the encrypted
  credentials in a dump are useless without it.

## 9. Security notes

- **agent-browser is closed without a token.** It drives a real browser, so
  without `AGENT_BROWSER_TOKEN` it refuses requests (open mode exists only for
  tests via `AGENT_BROWSER_ALLOW_OPEN`). Every POST must carry a matching
  `X-Agent-Token`; `/health` stays open. It also has an **SSRF guard**: private,
  loopback, link-local and metadata addresses (`localhost`, `10.x`,
  `169.254.169.254`, ...) are rejected with `400 private_or_internal_host`.
  Do not expose it publicly; restrict it to the API's network where possible.
- **The BFF holds the session.** The login route sets `ljm_session`
  (`HttpOnly; Secure; SameSite=Lax`); the JWT never reaches browser JavaScript
  and every browser-to-API call goes through `/api/proxy/[...path]`.
- **Secrets live only in the host's secret store or the encrypted vault.**
  `.env*` is git-ignored. Never commit service-account JSON, API keys,
  `CRON_SECRET`, `TENANT_CRED_KEY` or database URLs. Rotate `CRON_SECRET` on
  api, scheduler and frontend together.
- Set `CRON_SECRET` in production: cron routes are open when it is unset.
- Cron secret grants only ingest/trigger routes; owner-sensitive mail routes
  (`/mail/status`, `/mail/test-send`, `/mail/disconnect`, ...) require a user bearer.
- Change the seeded owner password on any shared or public deployment.
- `/docs` and `/openapi.json` are disabled when `APP_ENV=production`.

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| API container crashes at start with host-path shebang errors, or the venv is wrong | A host `.venv` was copied into the image. `backend/.dockerignore` must contain `.venv` (it does); add the same to any new build context (e.g. `frontend/`: `node_modules`, `.next`) and rebuild with `--build --no-cache`. |
| API refuses to boot: "Paid Gemini tier required" | `APP_ENV=production` without `GEMINI_API_KEY`. Set the key (paid tier) or use `APP_ENV=development` locally. |
| Queue jobs stay `queued` | No worker running and no drain cron. Start `worker` or schedule `/jobs/drain`. Check `GET /health?include=jobs`. |
| Worker not picking up changed env / new credentials or code | The worker caches settings at start. Restart it (`docker compose restart worker`) after env changes, after the api applied new migrations, or after rotating the vault key. |
| Worker errors with LISTEN/NOTIFY or `SET LOCAL` issues | It is pointed at a pooled URL. Give the worker the direct `DATABASE_URL_DIRECT`. |
| `sa_configured=false` after a restart | Vault priming failed at boot (see api log "startup priming failed"). Usually the vault key changed or `TENANT_CRED_KEY` differs between runs. Restore the key, or re-paste the JSON in Settings. |
| Topbar shows "Last scan didn't finish" | The most recent crawl run failed or timed out. Check api logs for `/crawl/run`, FMCSA token/limits, and `FMCSA_TIME_BUDGET_S`; re-run `POST /crawl/run` (safe to repeat). |
| Mail not syncing | `GET /mail/status`: check `mailbox_source`, `sa_configured`, `reason`, `read_mailboxes_count`. Confirm the three delegation scopes, that the Admin SDK is enabled, and (for all-mailbox mode) `GMAIL_ADMIN_IMPERSONATE`. Then backfill a small window (`hours: 2`). |
| Cron call returns 401 | `X-Cron-Secret` does not match the API's `CRON_SECRET`. |
| Browser CORS error | Add the frontend origin to `CORS_ALLOWED_ORIGINS`. |
| Login rate-limited for everyone | Frontend `LJM_PROXY_SECRET` and api `TRUSTED_PROXY_SECRET` are missing or differ. |
| Agent sources show disabled | `AGENT_BROWSER_URL` unset, token mismatch (401), or a source driver is `off`. Use Settings -> Agent-browser -> test. |
| agent-browser restarts / OOM | Instance too small for Chromium; use at least ~1 GB RAM. |

## 11. Development

```bash
# backend
cd backend
uv sync
cp .env.example .env            # set DATABASE_URL, CRON_SECRET, (GEMINI_API_KEY)
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload --port 8765
# http://127.0.0.1:8765/health

uv run pytest -q                       # SQLite fast path
TEST_HARNESS=pg16 uv run pytest -q     # real Postgres 16 (RLS + migration tests)
uv run lint-imports                    # module boundary contracts

# frontend
cd frontend
pnpm install
cp .env.example .env.local
pnpm dev -p 3100
pnpm gen:api                           # regenerate typed API client (offline)

# agent-browser
cd agent-browser && timeout 180 pytest tests/ -q
```
