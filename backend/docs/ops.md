# LJM Intelligence — ops notes

This is the operator's companion for running LJM Intelligence on free hosts
today (Vercel + Render + Neon) and on the user's own infrastructure tomorrow.
The whole shift is "start the `worker` container; delete the drain workflow."
No code change in between.

## Deployment surfaces

| Environment | Where things run | How the queue ticks |
|---|---|---|
| **Free (today)** | API on Render Docker free, DB on Neon free, frontend on Vercel free | GitHub Actions `queue-drain.yml` posts `/jobs/drain?seconds=25` every 5 min |
| **User's infra (later)** | `docker compose up -d` → `api` + `worker` + `postgres` | Always-on `worker` container drains continuously; `queue-drain.yml` deleted |

## Environment variables (new in this plan)

| Env var | Purpose | Where |
|---|---|---|
| `SEED_OWNER_PASSWORD` | Password hashed into the seed owner account on migration 0008. Production refuses to run without it. | Render dashboard + GitHub Actions migration jobs |
| `DATABASE_URL_DIRECT` | Neon **direct (non-pooler)** URL. The worker + Alembic use this. The pooler doesn't support `LISTEN/NOTIFY`. | Render dashboard (worker service), local Compose `.env` |
| `CRON_SECRET` | Already defined. Now also fronts `POST /jobs/drain`. | Render + GitHub Actions `queue-drain.yml` |

The queue layer adds **no other secrets**.

## Pool-sizing formula (R4)

```
workers_total_conns = workers × (DB_POOL_SIZE + DB_POOL_OVERFLOW)
api_conns          ≤ api_processes × (DB_POOL_SIZE + DB_POOL_OVERFLOW)
(workers_total_conns + api_conns) ≤ PG_MAX_CONNECTIONS - safety(≥10)
```

Defaults we ship: `DB_POOL_SIZE=3`, `DB_POOL_OVERFLOW=2`. With Neon-free's
~100 connection cap, four workers + one API process stays comfortably under:
`4 × 5 + 1 × 5 = 25 ≤ 90`.

Scaling: `docker compose up -d --scale worker=4` is the whole story. Procrastinate's
`FOR UPDATE SKIP LOCKED` is safe for any number of workers against one queue.

## R1–R5 production discipline

| Rule | Mechanism | Verify with |
|---|---|---|
| **R1** graceful shutdown | SIGTERM → finish current job → exit (procrastinate default) | `docker compose stop worker` logs "finishing current job" within 30s |
| **R2** idempotent jobs | Every `@app.task` docstring names its natural key; `queueing_lock` on periodics | Run any job twice; assert no duplicate side effect |
| **R3** periodic restart | `restart: always` in `docker-compose.yml`; `--max-tasks` + `--max-time` on CLI | `docker inspect worker` shows the policy |
| **R4** pool sizing | `DB_POOL_SIZE` + `DB_POOL_OVERFLOW` env; formula above | `docker compose up -d --scale worker=4` + `SELECT count(*) FROM pg_stat_activity` |
| **R5** horizontal scale | `--scale worker=N` on Compose | Multiple containers, one queue |

## Operations recipes

**Retry one failed job** (procrastinate CLI shipped in-container):

```sh
docker compose exec worker procrastinate --app=app.shared.queue.app retry <job_id>
```

**Purge old succeeded jobs** (succeeded rows kept 30 days):

```sh
docker compose exec postgres psql -U ljm -d ljm \
  -c "DELETE FROM procrastinate_jobs WHERE status = 'succeeded' AND scheduled_at < now() - interval '30 days';"
```

**One-click retry from the UI**: `POST /admin/jobs/{id}/retry` (owner-only).

## Alerting

`/health` returns `{jobs: {queued, running, failed_last_24h, oldest_queued_age_s}}`.
UptimeRobot (or any alerting of choice) should alert if `oldest_queued_age_s > 1800`.

## Portability contract

The whole target is "`docker compose up -d` on a stock Linux box boots the
full stack with no edits to `docker-compose.yml`." Secrets live in `.env`
beside it (chmod 600). Rotation = edit `.env` → `docker compose up -d`. No
cloud-vendor SDKs in the hot path. The DB is any Postgres reachable by
`DATABASE_URL`.
