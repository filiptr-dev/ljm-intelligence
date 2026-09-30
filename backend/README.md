# LJM Intelligence — backend

FastAPI + SQLAlchemy 2 (async) + Alembic on Neon Postgres. Deploys to Render (Docker, free).

## Local

```bash
cd backend
uv sync
cp .env.example .env  # then fill DATABASE_URL, GEMINI_API_KEY, CRON_SECRET
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
# → http://127.0.0.1:8000/health  → {"ok":true,"db":"up","app_env":"development"}
```

## Tests

```bash
uv run pytest
```

## Scheduler

The daily 3 PM America/New_York crawl is a GitHub Actions cron (see `.github/workflows/crawl.yml` at the repo root)
that POSTs to `/crawl/run` with the shared `CRON_SECRET`. GitHub Actions is free and cron-native — no Render cron
required. The frontend "Crawl now" button uses the same endpoint via a Next.js server action.

## FMCSA crawl-depth rollout (2026-09-30)

The FMCSA source now pages SODA with a **keyset paginator + derived frontier** (see plan
`projects/ljm-intelligence/plan/2026-09-30-fmcsa-crawl-depth.md` in the academy dossier).

**First prod run after this deploys = a backfill.** Expect:

- `counts.fmcsa_pages` at or near `FMCSA_BACKFILL_PAGE_CAP` (default 40 → ~20 000 discovered).
- `counts.fmcsa_stopped_reason` most likely `cap` or `timeout` on the first run.
- Every following run should settle to `fmcsa_pages` = 1–2 with `stopped_reason="caught_up"` and small `fmcsa_new`.

If `stopped_reason="timeout"` fires twice in a row, either lower `FMCSA_BACKFILL_PAGE_CAP`
or set `FMCSA_APP_TOKEN` (SODA per-app throttle bucket → faster pages). Gemini
scoring is unchanged (`GEMINI_SCORE_PER_RUN=10`); we're only exposing why it
skipped, via `counts.gemini_status` / `counts.gemini_error`.

OSM Overpass visibility (same slice): `counts.osm_status` is one of
`ok` | `partial` | `all_failed` | `skipped`, with the first failure preserved in
`counts.osm_error`. A single bad Overpass day never blocks the FMCSA pass.
