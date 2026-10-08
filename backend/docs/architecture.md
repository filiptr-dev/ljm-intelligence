# Architecture — LJM Intelligence

**Status:** onion/SOLID refactor complete (branch `refactor/onion`).
**Plan:** `symbiosis-brain/projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md`.

LJM Intelligence is a **modular monolith**. Industry-neutral platform with
freight-specific adapters; LJM is tenant #1, and every table, query and job
carries a `tenant_id`.

## Module map

```
app/
  identity/        orgs, users, auth, credentials, platform/tenant settings
  integrations/    connector registry, ports/, adapters/, loads, mail, crawl services
  prospecting/     freight: brokers, leads, shipper finder, scoring, pipeline/
  outreach/        call lists, email, capacity, unsubscribe
  inbox/           messages, threads, triage, mail rendering
  analysis/        AI intent/sentiment, usage metering, KPI/overview
  shared/          db, orm, repository base, queue, tenant, http, logging, rate limit
  auth/            token/password helpers + auth deps
```

## Onion layers (inside each module)

- **Outer (HTTP):** `*_router.py` / `router.py` + `schemas.py`. Thin: parse,
  call a service, return. Never import `repository`, other modules' `models`
  or `router`, or the `app.models` shim.
- **Application:** `*_service.py` / `service.py`. Orchestrate use cases against
  ports; never import FastAPI or SQLAlchemy query-builder symbols
  (`select/delete/update/func`) and never commit on their own.
- **Infrastructure:** `repository.py`, `models.py`, `adapters/`, `jobs.py`.
  All query building lives here.
- **Core:** `domain.py` and `integrations/ports/` are pure Python (no framework
  or sibling imports). Dependencies point inward only.

Freight adapters (FMCSA, DAT, CHR) are imported by `prospecting/` only.

## Enforcement

Contracts live in `backend/.importlinter` (routers vs routers/models/repository,
domain purity, services vs query builder, freight adapters, legacy packages).
Run `uv run lint-imports`.


The contracts live in `backend/.importlinter`; CI runs `uv run lint-imports`.

## Tenancy model

### Tables
`organizations` is the tenant table. `organization_members` links users.
`tenant_credentials` stores AES-GCM-encrypted per-connector secrets.
`tenant_feature_flags` stores per-tenant switches. `platform_settings` +
`tenant_settings` are the split-successor of the legacy `settings` singleton.

Every tenant-owned table has `tenant_id TEXT NOT NULL REFERENCES organizations(id)`
plus an index on `tenant_id`. Migration `0016_tenancy_foundation` added the
column (nullable), backfilled every existing row with the LJM tenant_id,
enforced NOT NULL + FK + index, and enabled Row-Level Security on each table.

### Tenant context flow

```
HTTP request
  → FastAPI dep `current_user`  (reads JWT from cookie)
  → dep `current_tenant`        (returns user.tenant_id as a TenantId)
  → bound into contextvar `app.shared.tenant._tenant_ctx`
  → `uow(sessionmaker, tenant)` opens session + begins txn + runs
      SET LOCAL app.tenant_id = '<ulid>'
  → service.py functions take `(session, …)` and read tenant from context
  → repository.py helpers filter every query by tenant_id (compile-time)
  → RLS backstop: every policy requires tenant_id match (or the admin sentinel)

Jobs (procrastinate, later):
  → each @task takes tenant_id as a first-class arg
  → the wrapper opens uow(…) with that tenant, SET LOCAL fires the same way
```

RLS policy template (installed on every tenant-owned table by migration 0016):

```sql
USING (
  current_setting('app.tenant_id', true) IS NULL
  OR current_setting('app.tenant_id', true) = ''        -- admin sentinel
  OR tenant_id = current_setting('app.tenant_id', true)
) WITH CHECK (
  current_setting('app.tenant_id', true) IS NULL
  OR current_setting('app.tenant_id', true) = ''
  OR tenant_id = current_setting('app.tenant_id', true)
)
```

**Belt + suspenders:** repositories filter by `tenant_id` for speed and clarity;
RLS catches a raw `session.execute(text("…"))` that forgets. One without the
other is not safe enough.

**Superuser caveat (tests):** Postgres bypasses RLS for superusers even with
`FORCE ROW LEVEL SECURITY`. Local verification used a non-superuser `app_user`
role. Neon production roles are non-superuser; RLS applies there as written.

## Integration contract (ports + adapters + registry)

Ports live in `app/integrations/ports/`:

- `EmailMailboxPort`, `EmailSenderPort` — read + write mail.
- `LoadBoardPort` — DAT/CHR/123LB/Truckstop.
- `AIProviderPort` — Gemini/Claude/Null.
- `EnrichmentPort` — FMCSA, future enrichment vendors.

Core modules depend only on these Protocols. Adapters live under
`app/integrations/adapters/<kind>/<vendor>.py` and may use their vendor's SDK
style. `ConnectorRegistry` (in `app/integrations/registry.py`) resolves ports
to adapters per tenant by reading `tenant_feature_flags` + `tenant_credentials`.

Credential encryption: AES-GCM per record; `TENANT_CRED_KEY` env (platform-level),
`tenant_credentials.secret_enc` holds `nonce || ciphertext || tag`.

## Frontend rules (planned, partially implemented)

- Next.js App Router, **server-first**. Pages are Server Components; islands
  are small `"use client"` children that handle interactivity.
- `lib/api/` typed openapi-fetch is the **only** data client; `server-only`
  imported from its entry so a stray client import fails the build.
- Auth lives in an **httpOnly cookie** set by the BFF login route; the browser
  never holds a bearer token. `/api/auth/me` does not return the JWT.
- Hand-rolled BFF proxies are deleted except `app/api/auth/*`; Server Actions
  or a minimal `/api/proxy/*` handle client-island mutations.
- `middleware.ts` → `proxy.ts` via Next 16 codemod.
- `lib/ai/*` and `lib/data/*` mocks are deleted — inbox analysis and inbox data
  are backend concerns served by `analysis/` and `inbox/`.

## Testing

- Existing suite runs on in-memory sqlite with `Base.metadata.create_all`.
- Migration round-trip is tested against **real Postgres 16** via
  `tests/test_migrations_pg.py`, opt-in with `DATABASE_URL_TEST_PG`. Local
  verification uses `docker run postgres:16`; CI should use the
  `services: postgres:16` lane with the same env.
- Full migration from sqlite harness to testcontainers Postgres is a follow-up
  (see "Deferred" in the dossier closeout).

## Operating knobs

- `DATABASE_URL` — Neon pooled URL for app traffic.
- `DATABASE_URL_DIRECT` — Neon direct URL for Alembic + queue worker (LISTEN/NOTIFY).
  **Planned, not yet wired** — add before the procrastinate worker lands.
- `TENANT_CRED_KEY` — 32-byte base64, AES-GCM key for `tenant_credentials.secret_enc`.
  **Not yet consumed** — adapter rehome introduces reads via `CredentialVault`.
## What this foundation delivers vs. what is deferred

**Delivered 2026-10-01:**

- `shared/` with `TenantId` NewType + contextvar, `uow()` (bound-param
  `set_config('app.tenant_id', :tid, true)`), `uow_admin()`.
- Three-layer tenant_id safety:
  1. `TenantMixin` (`app/shared/orm.py`) + SQLAlchemy `before_insert`
     listener stamps `tenant_id` from the contextvar on every ORM insert.
     Added to all 19 tenant-owned models in `app/models/__init__.py`.
  2. `server_default = LJM_TENANT_ID` on every `tenant_id` column (both
     the migration and the ORM mixin) catches raw-SQL inserts and the
     fresh-boot path with no context.
  3. RLS `ENABLE + FORCE` with `USING + WITH CHECK` policies on every
     tenant-owned table. Verified end-to-end on PG16 as a non-superuser
     role (Postgres superusers always bypass RLS even with FORCE; Neon
     production roles are non-superuser).
- Identity tables: `organizations`, `organization_members`,
  `tenant_credentials`, `tenant_feature_flags`, `platform_settings`,
  `tenant_settings`. LJM seeded as tenant #1 (deterministic id).
- `app/identity/credentials.py::CredentialVault` — AES-GCM per record,
  `TENANT_CRED_KEY` env (32-byte base64). `put/get` round-trip tested
  against the real `tenant_credentials` table.
- `app/identity/dependencies.py::current_tenant` — FastAPI dep that
  resolves user → tenant, binds the contextvar, and `set_config`s
  `app.tenant_id` on the session for RLS. Not yet attached to every
  router — attach in the service-extraction pass.
- `app/integrations/ports/{email,loadboard,ai,enrichment}.py` Protocol
  surfaces + `ConnectorRegistry` skeleton (accepts a `CredentialVault`
  + per-`(tenant, connector)` memo cache; `NotImplementedError` until
  the adapter rehome lands).
- `app/shared/http.py::build_shared_client` — one `httpx.AsyncClient`
  per process, wired in the FastAPI lifespan.
- `app/shared/logging.py` — `JsonFormatter`, `RequestIdMiddleware`
  (binds `request_id` into a contextvar, echoes `x-request-id` back
  on the response), `configure_json_logging`, `set_job_id`.
- `app/auth/tokens.py` — per-process `_jwt_secret_cache` so
  authenticated requests don't SELECT settings on every call.
- `app/config.py::database_url_direct` — optional Neon direct URL.
  `migrations/env.py` prefers it so Alembic uses the direct branch,
  not the pooler.
- import-linter contracts (`backend/.importlinter`): 4 kept, 0 broken.
  Analyzed 157 files, 535 dependencies.
- Opt-in PG16 test suites (`DATABASE_URL_TEST_PG=postgresql+psycopg://…`):
  * `tests/test_migrations_pg.py` — base↔head round-trip.
  * `tests/test_pg16_boot_and_tenancy.py` — real FastAPI boot on PG16,
    demo-owner login, `/brokers` + `/overview/today` + `/loads` all 200,
    ORM insert stamps tenant_id, raw INSERT hits the server_default.
  * `tests/test_tenancy_isolation.py` — four tests: repo scope, RLS
    backstop, cross-tenant WITH CHECK violation, admin sentinel.
  * `tests/test_credential_vault.py` — AES-GCM round-trip.
- `tests/test_fit_weights_drift.py` path fixed to the `(app)` route
  group — the pre-existing failure cleared.
- `frontend/src/middleware.ts` → `frontend/src/proxy.ts` via the Next 16
  `middleware-to-proxy` codemod. Build + lint + tsc all green.

**Deferred to follow-up work:**

The user's standing rule is no slicing; the honest truth is that the
remaining items are each a dedicated session's work. Listed here so the
next pass has a scope that fits a reasonable budget:

1. Rehome `app/mail/*`, `app/sources/loads/*`, `app/sources/provider.py`,
   `app/sources/fmcsa.py` under `app/integrations/adapters/*` and wire
   `ConnectorRegistry` to read from `CredentialVault`.
2. Split `app/models/__init__.py` (683 lines) into per-module `models.py`
   while keeping the re-export so Alembic autogenerate sees everything.
3. Extract `service.py` from every `app/api/*.py` router; delete
   `app/pipeline/run.py` `_Req`/`_AppState` hack. Attach `current_tenant`
   as a router-level dep so every authenticated request binds RLS.
4. Keyset-paginate `/brokers`, `/call-list`, `/overview`, `/shipper_finder`
   with a seeded 25k-leads-per-tenant perf test under 300 ms p95.
5. Swap the sqlite test harness to Postgres (testcontainers locally,
   GH Actions `services: postgres:16` for CI), removing the
   `with_variant(sqlite)` shims. Target: full suite on PG, not just the
   opt-in subset.
6. Frontend server-first rewrite of `call-list`, `shippers`, `leads`,
   `emails` (preserve sentiment UI exactly), `messages`, `campaigns`,
   `intelligence`, `settings`, and `emails/compose` — Server Component
   shells + `"use client"` islands.
7. Cookie-only auth: `/api/auth/me` stops returning the JWT; the typed
   `lib/api/` runs on the server, reads the cookie via `cookies()`,
   forwards `Authorization` to FastAPI. Needs the server-first rewrite
   above (otherwise every data call in the browser 401s).
8. `server-only` import on `lib/api/` entry (post-rewrite — would break
   today).
9. **Keep `lib/ai/*` and `lib/data/*`.** User override 2026-10-01: the
   `/emails` "Tone of broker emails" StackBar and per-row SentimentDot
   must render unchanged. Replace only when real sentiment feeds from
   the inbox + analysis backends.
10. Group `Settings` by feature with `validation_alias` (env names
    unchanged). Invasive — touches every `s.gmail_*` / `s.dat_*` /
    `s.chr_*` / `s.lb123_*` / `s.truckstop_*` call site.
11. Make `ai_usage_log` writes pass `tenant_id` explicitly (works today
    via the `TenantMixin` listener; explicit is cleaner).
12. structlog upgrade (today's `JsonFormatter` is deliberately
    stdlib-only); Sentry init.
13. procrastinate worker + `jobs.py` wrapper + same-transaction
    dispatch. Needs the service extraction above.

Note: the vault key bootstrap assumes a single process (Render, 1 worker); multiple workers would race on first-time key creation.
