# LJM Intelligence — frontend

Next.js 16 (React 19, Tailwind, shadcn) dashboard for **LJM International**.
Four product surfaces:

1. **Broker Intelligence**: semantic analysis of broker emails — who they work with most, who rejects them and why, reply-speed impact, best send times, lanes, rates, k-means segments.
2. **Lead Finder**: live crawler, streams FMCSA / shipper / forwarder leads from the backend and scores each against LJM's best partners (lookalike score). One-click outreach.
3. **New campaign** (`/outreach`): name + goal (replies, interested, new customers, loads), audience, AI-written message with personal fields, auto follow-ups, design, send-now / best-time / scheduled. **Reuse** copies any past campaign.
4. **Campaigns** (`/campaigns`): per-campaign goal progress, funnel, follow-up results, reply sentiment (interested, asked for rates, not now, OOO, not interested, unsubscribe), plus a cross-campaign "what works" ranking by audience, tone, launch day, subject.

Real data comes from the FastAPI backend (brokers, FMCSA leads, shipper
finder, enrichment, call list, overview, AI metering). The `/emails` surface
still uses `src/lib/ai/mock.ts` (and optionally `src/lib/ai/gemini.ts` if
`GEMINI_API_KEY` is set) for sentiment labelling — that will move into the
backend `analysis/` module when the inbox-analysis task lands.

## Run

```bash
pnpm install
cp .env.example .env.local        # then edit — see "Env" below
pnpm dev -p 3100                  # http://localhost:3100
```

Pages are React Server Components; interactive bits are small
`"use client"` islands. Sidebar navigation is client-side (`next/link`).

## Server-first + cookie-only auth

Login hits the BFF route `src/app/api/auth/login/route.ts`, which calls
FastAPI and sets a `ljm_session` cookie: `HttpOnly; Secure;
SameSite=Lax; Path=/`. The JWT is **never** in `document.cookie` or any
client bundle. Logout clears the cookie via `/api/auth/logout`.

The only client-island data path to the backend is same-origin through
`src/app/api/proxy/[...path]/route.ts`. It reads the HttpOnly cookie,
attaches `Authorization: Bearer <jwt>` upstream, and streams the response
back — no business logic, no re-serialisation. Server components import
the typed client from `src/lib/api/server.ts`, which imports
`server-only`, so a stray client import fails the build.

## Env

| Var | Where read | What for |
|---|---|---|
| `API_URL` | server only | Upstream the `/api/proxy/*` + server-component fetches target. In prod this is the Render URL; in dev `http://localhost:8765`. |
| `NEXT_PUBLIC_API_URL` | server + browser | Public API origin for link building and the OpenAPI client's base URL fallback. The browser does **not** hit it directly for data anymore — everything goes through `/api/proxy/*`. |
| `CRON_SECRET` | server only | Shared secret with FastAPI `/crawl/run`. Must match the backend. |

**Local dev:** `.env.local` (git-ignored) copies from `.env.example`.
Backend defaults to `:8765`; keep both sides aligned.

**Vercel Preview + Production:** set `API_URL` and `NEXT_PUBLIC_API_URL` to
`https://ljm-intelligence-api.onrender.com` in **Settings → Environment
Variables**, scoped to both environments. The backend CORS allowlist in
`backend/app/main.py` covers `https://ljm-intelligence.vercel.app` and
`http://localhost:3100`; per-deploy preview URLs are matched via
`CORS_ALLOWED_ORIGIN_REGEX` on the Render backend, e.g.:

```
CORS_ALLOWED_ORIGIN_REGEX=^https://ljm-intelligence-.*\.vercel\.app$
```

## Typed API client (`src/lib/api/`)

`client.ts` wraps `openapi-fetch` with an 8s timeout and exactly one retry
on 502/503/504 for GETs (POSTs never retry). The generated `schema.d.ts`
is committed so `tsc` runs offline. Regenerate whenever a backend route or
Pydantic model changes:

```bash
pnpm gen:api    # dumps FastAPI OpenAPI offline (in-memory SQLite), regenerates schema.d.ts
```

Under the hood it calls `backend/scripts/dump_openapi.py` with an
in-memory SQLite `DATABASE_URL` — never touches Neon, never starts a server.

## What's still mock / client-side

| Layer | File | Notes |
|---|---|---|
| Email sentiment (mock) | `src/lib/ai/mock.ts` | Pattern + lexicon pass over the email text — extracts intent, sentiment, rate, lane, equipment, rejection reason. Used by `/emails`. |
| Email sentiment (Gemini) | `src/lib/ai/gemini.ts` | Same interface; `AI_PROVIDER=gemini GEMINI_API_KEY=…` switches. Moves into backend `analysis/` with the inbox-analysis task. |
| Reply AI (mock) | `src/lib/ai/replies.ts` | Reply categorisation + sentiment. Same migration path. |
| Live crawler engine | `src/components/app/engine.tsx` | Client-side stream + scheduled send simulation for the Lead Finder demo surface. |
| Campaign analysis | `src/lib/campaigns/metrics.ts` | Per-campaign + cross-campaign "what works". |
| Campaign history (dummy) | `src/lib/campaigns/history.ts` | 6 months of generated history for `/campaigns` demos. |

Everything else — brokers, shippers, call list, enrichment, overview, AI
usage, auth — reads from the backend.

## Branding

The client is `CLIENT` in `src/lib/data/types.ts`. The product mark and
lockup live in `src/components/brand/marks.tsx` (`LogoMark`,
`LogoMarkCompact`, `Wordmark`). The full-lockup SVG is mirrored at
`public/ljm-intelligence.svg` and the favicon at `src/app/icon.svg` — all
`currentColor`, so one Tailwind `text-*` class drives the mark's colour on
any surface.
