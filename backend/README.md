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
