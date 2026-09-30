# LJM Intelligence: broker intelligence demo

First-meeting sales artefact for **LJM International** — a Lincoln Park, NJ dry-van
carrier running the eastern US. It shows four things:

1. **Broker Intelligence**: semantic analysis of ~2,700 broker emails. Shows who they work with most, who rejects them and why, reply-speed impact, best send times, lanes, rates and k-means broker segments.
2. **Lead Finder**: a crawler that is "always on", streams new broker / shipper / forwarder leads and scores each one against LJM's best partners (lookalike score). Includes one-click outreach.
3. **New campaign** (`/outreach`): name and goal (replies, interested replies, new customers, loads), audience (new leads, existing brokers or both, with search), an AI-written message with plain-English personal fields, automatic follow-ups to non-responders, design, and send now / at the best time / at a picked date. **Reuse** copies any past campaign.
4. **Campaigns** (`/campaigns`): every campaign with goal progress, funnel, follow-up step results and each reply tagged by AI sentiment (interested, asked for rates, not now, out of office, not interested, unsubscribe). The "what works" analysis ranks campaign types, audiences, tones, launch days and subject lines. It includes 6 months of dummy history (`src/lib/campaigns/history.ts`).

All data in this rev is dummy data, generated with a fixed seed. Later revs will
stream real registrations from the FMCSA Company Census SODA API into the Lead
Finder alongside the seeded pool.

## Run

```bash
pnpm install
cp .env.example .env.local        # then edit — see "Env" below
pnpm dev -p 3100                  # http://localhost:3100
```

Tip for the meeting: move between pages with the sidebar (client-side navigation). A full browser reload restarts the live crawler session. Campaigns survive reloads (localStorage; key `ljm.campaigns.v1`).

## Env

Two backend URLs, one server-only and one browser-safe. Both point at the same
API host today; the split leaves room for a shorter internal hostname later.

| Var | Where read | What for |
|---|---|---|
| `BACKEND_URL` | server only | Legacy `/api/*` proxy handlers (crawler + email). Never exposed to the browser so `CRON_SECRET` can travel through it. |
| `NEXT_PUBLIC_API_URL` | server + browser | Base URL of the FastAPI backend for the typed `lib/api/` client. The Shipper Finder page (`/shippers`) calls it from the browser directly. |
| `CRON_SECRET` | server only | Shared secret with the FastAPI `/crawl/run` endpoint. |

**Local dev:** `.env.local` (git-ignored) copies from `.env.example`; both URLs
default to `http://localhost:8765`. The backend runs on `:8000` or `:8765`
depending on your `uvicorn` invocation — set the port on both sides to match.

**Vercel Preview + Production:** add `NEXT_PUBLIC_API_URL` under
`Settings → Environment Variables`, scoped to both **Preview** and
**Production**, value = `https://ljm-intelligence-api.onrender.com` (the Render
API URL). The backend's CORS allowlist already includes the canonical Vercel
origin `https://ljm-intelligence.vercel.app` and localhost dev — see
`backend/app/main.py`.

**Vercel Preview CORS:** per-deploy preview origins (e.g.
`ljm-intelligence-abc123-vercel.app`) are NOT covered by the hardcoded origin
list. To allow them, add this env var on the Render backend:

```
CORS_ALLOWED_ORIGIN_REGEX=^https://ljm-intelligence-.*\.vercel\.app$
```

The `cors_allowed_origin_regex` field in `backend/app/config.py` reads this
variable and passes it to FastAPI's `CORSMiddleware.allow_origin_regex`.

## Typed API client (`src/lib/api/`)

Mirrors the pattern from `bbunikoop-demo/frontend/lib/api/`. `client.ts` wraps
`openapi-fetch` with an 8s timeout and exactly one retry on 502/503/504 for GETs
(POSTs never retry — a duplicate promote is a duplicate promote). The generated
`schema.d.ts` is committed so `tsc` is offline. Regenerate whenever a backend
route or Pydantic model changes:

```bash
pnpm gen:api    # dumps FastAPI OpenAPI offline (SQLite in-memory), regenerates schema.d.ts
```

Under the hood it calls `backend/scripts/dump_openapi.py` with an in-memory
SQLite `DATABASE_URL` — it never touches the production Neon database and never
spins up a server.

## How it works

| Layer | File | Notes |
|---|---|---|
| Dummy data | `src/lib/data/generate.ts` | 150 brokers, 18 months of load threads: offers, quotes, bookings, rejections, invoices, complaints. Hidden "persona" drives behaviour. |
| AI (mock) | `src/lib/ai/mock.ts` | Reads the email **text** (patterns and a sentiment lexicon) and extracts intent, sentiment, rate, lane, equipment and rejection reason. The analysis never sees the hidden labels. |
| AI (Gemini) | `src/lib/ai/gemini.ts` | Same interface. Set `AI_PROVIDER=gemini GEMINI_API_KEY=…` to switch. Not tested yet. |
| Data science | `src/lib/analytics/` | Thread reconstruction, per-broker stats, health score, k-means (k-means++ init, z-scored features), cosine-similarity lookalike scoring, reply-time and send-time analysis. |
| Live engine | `src/components/app/engine.tsx` | Client-side crawler stream, auto-outreach, scheduled sends, follow-ups, delivery/open/reply/won simulation. |
| Reply AI (mock) | `src/lib/ai/replies.ts` | Reads each reply and returns category + sentiment. |
| Campaign analysis | `src/lib/campaigns/metrics.ts` | Per-campaign summary, goal progress, and the cross-campaign "what works" insights. |

## Branding

The client is `CLIENT` in `src/lib/data/types.ts`. The product mark and lockup
live in `src/components/brand/marks.tsx` (`LogoMark`, `LogoMarkCompact`,
`Wordmark`). The full-lockup SVG is also mirrored at `public/ljm-intelligence.svg`
and the favicon at `src/app/icon.svg` — all `currentColor`, so one knob (a
Tailwind `text-*` class) drives the mark's colour on any surface.
