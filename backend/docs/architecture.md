# Architecture — LJM Intelligence

**Status:** foundation laid 2026-10-01, modularisation in progress.
**Approved plan:** `symbiosis-brain/projects/ljm-intelligence/plan/2026-10-01-architecture-foundation-tenant-ready.md`.

LJM Intelligence is a **modular monolith** with strict DDD boundaries. We are an
industry-neutral platform with freight-specific adapters bolted on; LJM is
tenant #1 and the only tenant for v1, but every table, every query, and every
job already carries a `tenant_id` so growth into a multi-tenant SaaS does not
require re-stamping every row.

## Module map

Seven top-level modules under `backend/app/`:

```
app/
  identity/        orgs (tenants), users, auth, tenant context
  integrations/    connector registry + ports + adapters (email, loadboard, ai, enrichment)
  prospecting/     freight-specific: find brokers/shippers/leads, FMCSA
  outreach/        industry-neutral: call lists, enrichment, auto-send
  inbox/           messages, threads (uses an integrations email port)
  analysis/        AI intent/sentiment, per-tenant usage metering
  shared/          db/session, logging, http clients, tenant context, events
```

Each module uses a Python-idiomatic DDD file layout (fastapi-best-practices +
Cosmic Python): `router.py`, `schemas.py`, `models.py`, `service.py`,
`dependencies.py`, `repository.py`, with `domain.py` only where a real
Value Object or invariant lives, and `jobs.py` added when queue work arrives.

## Layer rules (enforced by import-linter in `.importlinter`)

1. **`router.py`** may import this module's `schemas`, `service`, `dependencies`,
   and other modules' `service` or ports. It never imports another module's
   `router`, `models`, or `repository`.
2. **`service.py`** takes a session; never commits itself. Imports this
   module's `models`, `repository`, `domain`, and other modules' services or
   domain events. Never imports FastAPI.
3. **`domain.py`** is pure Python. No FastAPI, no SQLAlchemy, no siblings.
4. **Freight adapters** (FMCSA, DAT, CHR, …) are only imported by
   `prospecting/`. Every other core module is industry-neutral.

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

- `shared/` with tenant contextvar, `uow()`, `uow_admin()`.
- `organizations`, `organization_members`, `tenant_credentials`,
  `tenant_feature_flags`, `platform_settings`, `tenant_settings` tables.
- `tenant_id` on all 19 tenant-owned tables, backfilled to LJM.
- RLS policies enabled + FORCED on every tenant-owned table.
- Module skeletons for `identity`, `integrations`, `prospecting`, `outreach`,
  `inbox`, `analysis`, with the file-layout pattern reserved per module.
- `integrations/ports/*` Protocols and the `ConnectorRegistry` skeleton.
- `import-linter` contracts covering routers, cross-module models, pure
  `domain.py`, and freight-adapter scope — all 4 contracts kept.
- Migration round-trip test on PG16 (opt-in).

**Deferred to follow-up work:**

- Rehome of existing adapters (`app/mail/*`, `app/sources/loads/*`,
  `app/sources/provider.py`, `app/sources/fmcsa.py`) under
  `integrations/adapters/*` behind their ports.
- Splitting `app/models/__init__.py` (683 lines) into per-module `models.py`
  while keeping the re-export for Alembic autogenerate.
- Extracting `service.py` from each legacy `app/api/*.py` router and deleting
  the `app/pipeline/run.py` `_Req`/`_AppState` hack.
- Full-suite migration from in-memory sqlite to testcontainers Postgres.
- Keyset pagination on `/brokers`, `/call-list`, `/overview`, `/shipper_finder`
  + the 25k-lead-per-tenant perf test.
- Tenant isolation test suite (repo-level, RLS backstop, cross-tenant write,
  cross-tenant job) — the behaviour is proven at the DB layer by migration
  0016 and manually verified during the foundation pass.
- Frontend server-first rewrite of the 8 fully-client pages.
- Cookie-only auth refactor; deletion of `lib/ai/*` and `lib/data/*` mocks.
- Structured logging + Sentry + `DATABASE_URL_DIRECT` wiring.
- `CredentialVault` service + per-tenant credential reads.
- `Settings` grouping-by-feature with `validation_alias`.
