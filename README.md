# LJM Intelligence

Broker intelligence, lead finder, and outreach engine for **LJM International**
— a Lincoln Park, NJ dry-van carrier. One dashboard, one API, real data.

| Folder | What | Runs on |
|---|---|---|
| `backend/` | FastAPI + SQLAlchemy 2 async + Alembic on Neon Postgres. Modular monolith (see below). | Render (Docker, `render.yaml`) |
| `frontend/` | Next.js 16 (React 19, Tailwind, shadcn). Server-first — the browser never holds a bearer token. | Vercel (Root Directory `frontend`) |

The app is **tenant-ready**. LJM is tenant #1; the schema (`organizations`,
`organization_members`, `tenant_credentials`, `tenant_feature_flags`,
`platform_settings`, `tenant_settings`) and per-row `tenant_id` + Postgres RLS
are already in place so a second tenant doesn't require another migration pass.

## Backend — modular monolith

`backend/app/` is split into seven industry-neutral + freight-specific modules.
Each has a thin `router.py`, `schemas.py`, `models.py`, a service module, and
only promotes `domain.py` where a real Value Object or invariant earns it.

```
app/
  identity/        orgs (tenants), users, auth, tenant context, credential vault
  integrations/    connector registry + ports + adapters (email, loadboard, ai, enrichment)
  prospecting/     freight: brokers / shippers / leads / FMCSA / Overpass / enrichment
  outreach/        industry-neutral: call lists, auto-send, unsubscribe, brand
  inbox/           messages, threads (via an integrations email port)
  analysis/        AI intent/sentiment, per-tenant usage metering
  shared/          db/session, logging, http clients, tenant context, config
```

**Boundaries are enforced** by [import-linter](./backend/.importlinter) — four
contracts from the architecture plan:

1. Routers never import another module's router.
2. Routers never import another module's `models.py`.
3. `domain.py` imports no framework or sibling module (pure Python).
4. Only `prospecting/` may import freight-specific adapters (FMCSA, loadboards).

Run it locally:

```bash
cd backend
uv run lint-imports
```

### Run locally

```bash
cd backend
uv sync
cp .env.example .env            # then fill DATABASE_URL, GEMINI_API_KEY, CRON_SECRET
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload --port 8765
# → http://127.0.0.1:8765/health  → {"ok":true,"db":"up","app_env":"development"}
```

### Tests

```bash
# Fast path: SQLite (aiosqlite). Default pytest config excludes perf tests.
cd backend
uv run pytest -q

# Real Postgres 16 via docker (reused container ljm-test-pg16 on :5444).
# Required for the tenancy RLS backstop + migration round-trip tests.
TEST_HARNESS=pg16 uv run pytest -q

# Opt-in performance benchmarks (25k leads × 2 tenants, p95 < 300ms targets).
uv run pytest -m perf
```

Alembic heads: `uv run alembic heads` (currently `0016`). Migrations are the
schema source of truth — `Base.metadata.create_all` is only used by the
SQLite dev harness.

### Env vars (operator-facing)

The architecture and the authoritative env-var list live in
[`docs/architecture.md`](./docs/architecture.md); the longer module-level
breakdown is in [`backend/docs/architecture.md`](./backend/docs/architecture.md).
Shortlist:

- **Core:** `DATABASE_URL`, `DATABASE_URL_DIRECT` (optional — Alembic / future
  LISTEN/NOTIFY worker), `APP_ENV`, `LOG_LEVEL`, `CORS_ALLOWED_ORIGINS`,
  `CORS_ALLOWED_ORIGIN_REGEX`, `FRONTEND_ORIGIN`.
- **Auth:** `AUTH_JWT_SECRET` (optional override — the signing secret is
  minted once by migration 0008 and lives on the `settings` row),
  `AUTH_ACCESS_TTL_DAYS`.
- **AI providers:** `GEMINI_API_KEY`, `GEMINI_MODEL*`, `ANTHROPIC_API_KEY`,
  `CLAUDE_DEFAULT_MODEL`, `CLAUDE_CHEAPER_MODEL`.
- **FMCSA crawl:** `FMCSA_APP_TOKEN`, `FMCSA_PAGE_SIZE`,
  `FMCSA_PER_RUN_PAGE_CAP`, `FMCSA_BACKFILL_PAGE_CAP`, `FMCSA_TIME_BUDGET_S`.
- **Mail (Google Workspace, env-gated; default simulated):** `MAIL_SENDER`,
  `MAILBOX_SOURCE`, `MAIL_OWNER_SEND_ENABLED`, `GMAIL_SA_JSON`,
  `GMAIL_IMPERSONATE`, `GMAIL_ADMIN_IMPERSONATE`, `GMAIL_PER_MAILBOX_RPS`,
  `GMAIL_GLOBAL_RPS`, `GMAIL_BACKOFF_MAX_SECONDS`.
- **Outreach / unsubscribe:** `SIMULATED_DELIVERY`, `OUTREACH_FROM_EMAIL`,
  `OUTREACH_FROM_NAME`, `OUTREACH_POSTAL_ADDRESS`, `UNSUBSCRIBE_BASE_URL`,
  `UNSUBSCRIBE_SECRET`.
- **Load boards** (each board is ON only when all three of its vars are set):
  `DAT_SERVICE_ACCOUNT_EMAIL` / `DAT_SERVICE_ACCOUNT_PASSWORD` / `DAT_ORG_ID`;
  `CHR_CLIENT_ID` / `CHR_CLIENT_SECRET` / `CHR_CARRIER_CODE`;
  `LB123_API_KEY` / `LB123_CARRIER_USERNAME` / `LB123_CARRIER_PASSWORD`;
  `TRUCKSTOP_INTEGRATION_ID` / `TRUCKSTOP_USERNAME` / `TRUCKSTOP_PASSWORD`.
- **Scheduler:** `CRON_SECRET` (shared with the GitHub Actions daily crawl in
  `.github/workflows/daily-crawl.yml`).
- **Observability (optional):** `SENTRY_DSN` (unset = SDK not initialised,
  dev + CI run clean), `SENTRY_TRACES_SAMPLE_RATE` (default `0.0`).

Env names are stable: each feature block is a `pydantic-settings` sub-model
with a fixed `env_prefix` (`GMAIL_`, `DAT_`, `CHR_`, `LB123_`, `TRUCKSTOP_`),
so the names above won't drift as the code reshapes.

## Frontend — server-first Next.js

Pages are **React Server Components** that call the backend from the server
via a typed `lib/api/` client (`openapi-fetch` + a generated
`schema.d.ts`). Interactive bits are small `"use client"` islands.

**Auth — HttpOnly cookie only.** Login hits the Next.js BFF route
(`src/app/api/auth/login/route.ts`), which calls FastAPI and sets an
`ljm_session` cookie: `HttpOnly; Secure; SameSite=Lax; Path=/`. The JWT is
never in `document.cookie` or any client bundle, which closes the XSS token
exfiltration path.

**One client data path — `/api/proxy/[...path]`.** The only way a client
island reaches the backend is same-origin through
`src/app/api/proxy/[...path]/route.ts`. The proxy reads the HttpOnly cookie,
attaches `Authorization: Bearer <jwt>`, and streams the response back — no
business logic, no shape rewriting. There is no browser-side
`NEXT_PUBLIC_API_URL` fetch anymore. The `server-only` package is imported
from `lib/api/server.ts` so a stray client import fails the build.

### Run locally

```bash
cd frontend
pnpm install
cp .env.example .env.local       # see "Env" in frontend/README.md
pnpm dev -p 3100                 # http://localhost:3100
```

### Regenerate the typed API client

The OpenAPI schema is dumped offline against an in-memory SQLite — it never
touches Neon and never spins up a server:

```bash
cd frontend
pnpm gen:api
```

## Deploy

| Target | Config | Notes |
|---|---|---|
| Backend | [`render.yaml`](./render.yaml) | Docker, `backend/Dockerfile`. All secrets `sync: false` — set per-service in the Render dashboard. Health check: `/health`. |
| Frontend | Vercel project, Root Directory `frontend` | Set `API_URL` (server-only; the proxy's upstream) and keep `NEXT_PUBLIC_API_URL` only for same-origin link building. CORS allowlist is in `backend/app/main.py`. |
| Scheduler | `.github/workflows/daily-crawl.yml` | GitHub Actions cron, 3 PM `America/New_York` → `POST /crawl/run` with `X-Cron-Secret`. |

## Architecture

The modular layout, tenancy model, port/adapter contract, and the
server-first auth story all live in [`docs/architecture.md`](./docs/architecture.md)
(repo-local, the one a developer reading freight-demo cold should start with).
The deeper module-level detail is in
[`backend/docs/architecture.md`](./backend/docs/architecture.md).

More detail on the frontend (data path, branding, dev-only mocks) in
[`frontend/README.md`](frontend/README.md).
