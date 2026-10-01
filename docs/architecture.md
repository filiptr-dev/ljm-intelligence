# Architecture — LJM Intelligence

**Status:** foundation shipped 2026-10-01, modularisation in progress.
**Shape:** modular monolith, strict DDD boundaries, tenant-ready from day one.

LJM is tenant #1 and the only tenant for v1, but every table, every query,
and every job already carries a `tenant_id`. Growth into a multi-tenant
SaaS does not require re-stamping every row — it only requires turning on
the signup/billing/admin surfaces that were deliberately deferred.

See also the longer, module-level doc at
[`backend/docs/architecture.md`](../backend/docs/architecture.md).

---

## Module map

Seven top-level modules under `backend/app/`:

```
app/
  identity/        orgs (tenants), users, auth, tenant context, CredentialVault
  integrations/    connector registry + Ports + adapters (email, loadboard, ai, enrichment)
  prospecting/     freight-specific — find brokers/shippers/leads, FMCSA
  outreach/        industry-neutral — call lists, enrichment, auto-send
  inbox/           messages, threads (reads an integrations email port)
  analysis/        AI intent/sentiment, per-tenant usage metering
  shared/          db/session, logging, sentry, http clients, tenant context, events
```

Each module uses a Python-idiomatic DDD layout
(fastapi-best-practices + Cosmic Python): `router.py`, `schemas.py`,
`models.py`, `service.py`, `dependencies.py`, `repository.py`, with
`domain.py` only where a real Value Object / invariant lives, and `jobs.py`
when queue work arrives.

## Router → service → repository

The one-direction pattern across every module:

- **Router** (FastAPI): auth, pagination, validation. Thin — no business
  logic, no DB. Delegates to the module's `service.py`.
- **Service**: the use case. Takes a session (never commits), calls
  repositories, raises domain exceptions. Idempotent where it needs to be.
- **Repository**: the SQL seam. Query / insert / update, nothing else. One
  repo per aggregate; keyset pagination for list reads over the hot tables.

Routes that touch ≥2 modules go through `app.db.uow()` — opens a session,
begins a transaction, binds the tenant, hands the session to the services,
commits on success.

## Import-linter contracts

`uv run lint-imports` enforces the module boundary at build time, not in
PR review. Four contracts, 0 broken on main:

1. **Routers never import other modules' routers** — composition happens in
   `app.main.create_app`, not module-to-module.
2. **Routers never import other modules' models** — cross-module reads go
   through the owning service.
3. **`domain.py` imports no framework or sibling module** — the domain stays
   pure Python so it can be exercised without a DB or an app.
4. **Only `prospecting/` imports the freight-specific adapters (FMCSA)** —
   every other module stays industry-neutral.

A boundary without a machine check is a suggestion.

## Tenancy

Belt-and-suspenders by design:

- **`tenant_id` column** on every tenant-owned table, `NOT NULL`, FK to
  `organizations.id`, with a composite index on
  `(tenant_id, <hot sort column>)`.
- **`TenantMixin`** attaches the column + default + index to a model with
  one base class. New models cannot forget the column.
- **Postgres RLS** on every tenant-owned table:
  `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY`. The policy
  reads `current_setting('app.tenant_id', true)`. A non-superuser `app_user`
  role in production and in the PG16 test harness means the policy actually
  bites — superusers bypass RLS even with `FORCE`.
- **`app_user` role** — Neon production app roles are non-superuser by
  design. The test suite mirrors prod via a dedicated non-superuser role
  so the four isolation tests prove the DB-layer backstop.
- **`uow()` + `set_config('app.tenant_id', :tid, true)`** — bound parameter
  (no injection), transaction-scoped (`is_local=true`) so the setting is
  PgBouncer-safe and gets wiped on commit/rollback. The admin sentinel
  (`app.tenant_id = ''`) is set only by `uow_admin()` for ops/cron paths
  and matches the RLS policy's cross-tenant branch.

## Cookie-only auth + the single data path

Login hits the Next.js BFF route `src/app/api/auth/login/route.ts`, which
calls FastAPI and sets an `ljm_session` cookie:
`HttpOnly; Secure; SameSite=Lax; Path=/`. The JWT is never in
`document.cookie` or any client bundle, which closes the XSS token
exfiltration path.

The only way a client island reaches the backend is same-origin through
`src/app/api/proxy/[...path]/route.ts`. The proxy reads the HttpOnly cookie,
attaches `Authorization: Bearer <jwt>`, and streams the response back — no
business logic, no shape rewriting, no `NEXT_PUBLIC_API_URL` fetch in the
browser. The `server-only` package is imported from `lib/api/server.ts` so a
stray client import fails the build.

The `X-Cron-Secret` allowlist in the proxy is an exact string match on
`joinedPath === "crawl/run" && method === "POST"`: the one route the
GitHub Actions cron needs, and nothing else.

## Logging + Sentry

Structured JSON logging via `app/shared/logging.py` — one JSON line per
record, with `request_id` and `tenant_id` pulled from contextvars bound by
the request middleware and `uow()`.

Sentry is wired in `app/shared/sentry.py` and initialised in the FastAPI
lifespan **only when `SENTRY_DSN` is set**, so dev + CI run clean without
touching the network. A `before_send` hook tags every outgoing event with
`request_id` / `job_id` / `tenant_id` from those same contextvars, so the
HTTP path and the background crawl path produce identically-shaped events.
`capture_exception(exc)` is a thin wrapper the crawl pipeline calls from
its top-level `except Exception` so jobs report alongside HTTP errors.

## Test harnesses

Two orthogonal harnesses:

- **sqlite in-memory** (default): fast, hermetic, no containers. Covers
  logic that does not depend on PG-specific behaviour (RLS, JSONB
  operators, keyset indexes). Prod-safety guard in `conftest.py` aborts
  collection if `DATABASE_URL` is anything but a local/sqlite URL.
- **PG16** (`TEST_HARNESS=pg16` or `DATABASE_URL_TEST_PG=…`): spins up or
  reuses a throwaway `ljm-test-pg16` container on `:5444`, applies
  `alembic upgrade head` from empty, and runs the full suite as the
  non-superuser `app_user` role. The four tenancy isolation tests, the
  perf benchmarks, and the migration round-trip test only run here.

Opt-in perf benchmarks (`uv run pytest -m perf`) seed 25k leads × 2
tenants and assert p95 < 300ms on the hot list endpoints with keyset
pagination and the composite indexes in place.
