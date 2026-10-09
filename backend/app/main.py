"""FastAPI app factory. Run locally with `uvicorn app.main:create_app --factory --reload`."""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db import create_engine, create_sessionmaker, ping

log = logging.getLogger(__name__)


class JobsHealth(BaseModel):
    queued: int = 0
    running: int = 0
    failed_last_24h: int = 0
    oldest_queued_age_s: int = 0


class Health(BaseModel):
    ok: bool
    db: Literal["up", "down"]
    app_env: str
    jobs: JobsHealth | None = None


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings)

    # Sentry — no-op when `SENTRY_DSN` is unset. Init before the app is
    # built so framework integrations (FastAPI/Starlette) attach correctly.
    # Dev + CI run clean without the DSN; prod sets it on Render.
    from app.shared.sentry import init_sentry

    init_sentry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        # Shared httpx client — one pool per process so TCP+TLS reuse amortises
        # across adapters (FMCSA, OSM, Gemini, Claude, Gmail, load boards).
        from app.shared.http import build_shared_client

        app.state.http = build_shared_client()
        # Prime the Gmail SA JSON cache from the vault — the sync mail factory
        # functions consult this cache when ``GMAIL_SA_JSON`` env is unset, so
        # pasting creds in Settings works with zero env configuration. No-op
        # when nothing has been stored yet.
        try:
            from app.integrations.adapters.email.credentials import prime_from_vault

            await prime_from_vault(app.state.sessionmaker)
        except Exception:  # noqa: BLE001  pragma: no cover
            pass
        # Prime the per-source driver + agent-kill overlay from the settings
        # row so the registry can resolve env → DB → default without opening
        # a session on every ``/loads/sources`` call. Absent-safe: a missing
        # row leaves the overlay empty and env/default still win.
        try:
            from app.integrations.adapters.loadboard.registry import (
                prime_overlay_from_db,
            )

            await prime_overlay_from_db(app.state.sessionmaker)
        except Exception:  # noqa: BLE001  pragma: no cover
            pass
        try:
            yield
        finally:
            await app.state.http.aclose()
            await engine.dispose()

    # S0.1 — close /docs + /openapi.json in production. The repo is public; a
    # free route-map on a public URL is unnecessary attack surface. Dev + test
    # keep them so the local workflow is unchanged.
    _is_prod = settings.app_env == "production"

    # AC-A3 — Paid Gemini assertion. Free-tier Gemini uses prompt content for
    # training; paid does not. In production, refuse to boot if any mapped
    # feature points at a free-only model id (currently any ``*-free`` / `*-flash`
    # id without an api key is a strong signal). We're conservative: require
    # the API key to be set in prod and the configured model to not match the
    # ``free`` substring. This is the one place the privacy posture is enforced
    # before mail flows.
    if _is_prod:
        _gemini_key = getattr(settings, "gemini_api_key", None)
        _key_str = _gemini_key.get_secret_value() if _gemini_key and hasattr(_gemini_key, "get_secret_value") else _gemini_key
        if not _key_str:
            raise RuntimeError(
                "Paid Gemini tier required in production — set GEMINI_API_KEY (AC-A3)."
            )
        try:
            from app.integrations.adapters.ai.provider import DEFAULT_FEATURES as _AI_FEATS
            for _feat, _cfg in _AI_FEATS.items():
                _model = str(_cfg.get("model", ""))
                if "free" in _model.lower():
                    raise RuntimeError(
                        f"Paid Gemini tier required — feature {_feat} is on free-tier model {_model} (AC-A3)."
                    )
        except ImportError:
            pass
    app = FastAPI(
        title="LJM Intelligence API",
        version="0.1.0",
        summary="Real lead crawler + Gemini discovery/scoring for LJM International.",
        lifespan=lifespan,
        docs_url=None if _is_prod else "/docs",
        openapi_url=None if _is_prod else "/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings

    # Request-id middleware — binds a short id into a contextvar for logs and
    # echoes it back as `x-request-id` on the response.
    from app.shared.logging import RequestIdMiddleware

    app.add_middleware(RequestIdMiddleware)

    # CORS: Vercel origin(s) + regex for preview deploys, from env.
    # Slice 3: the shipper-finder page (Next :3100) hits the FastAPI origin
    # directly through the typed `lib/api/` client — no `/api/*` proxy — so
    # the browser needs the API-side allowlist to include both the dev port
    # and the canonical Vercel production origin. These are always merged in
    # (dedup preserved) so a missing env var doesn't silently break CORS.
    # localhost:3000 / 3030 / 3100 all appear in dev depending on which Next
    # dev server is up; keep the trio in the hardcoded fallback so the typed
    # `lib/api/` client's CORS preflight never fails on local dev regardless
    # of which port Next picked. The AI providers panel + shipper-finder both
    # go direct-to-:8765 and need this allowlist to include the frontend
    # origin, otherwise the browser's OPTIONS preflight 400s.
    # Gate the localhost trio behind a dev/test env — in production we never
    # want a page served on localhost to pass CORS preflight (reviewer finding,
    # 2026-10-01). ``Settings.app_env`` is a Literal of ("development", "test",
    # "production"), so this exhausts the non-prod set.
    _finder_origins: list[str] = ["https://ljm-intelligence.vercel.app"]
    if settings.app_env in ("development", "test"):
        _finder_origins = [
            "http://localhost:3000",
            "http://localhost:3030",
            "http://localhost:3100",
            *_finder_origins,
        ]
    _merged_origins = list(settings.cors_allowed_origins) + [
        o for o in _finder_origins if o not in settings.cors_allowed_origins
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_merged_origins,
        allow_origin_regex=settings.cors_allowed_origin_regex,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Cron-Secret"],
        max_age=600,
    )

    router = APIRouter()

    @router.get("/health", response_model=Health)
    async def health(request: Request, include: str | None = None) -> Health:
        """Readiness: process + database only. Cheap by default.

        Earlier revisions always ran the ``jobs_health`` aggregate over
        ``procrastinate_jobs`` on every call — a second DB round-trip that
        on Neon cold-wake would blow Render's health-probe window and mark
        the service unhealthy under load. Jobs stats are now opt-in via
        ``?include=jobs`` (the ``/admin/jobs`` page requests them); the
        Render probe just hits the no-param form and gets ``{ok, db}``.
        Never 500 — return ``db:'down'`` so the frontend can decide.
        """
        s: Settings = request.app.state.settings
        db_ok = False
        try:
            async with asyncio.timeout(s.db_health_timeout):
                await ping(request.app.state.engine)
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("health: db unreachable: %s", exc)
        jobs: JobsHealth | None = None
        if db_ok and include and "jobs" in include.split(","):
            try:
                from app.shared.queue import jobs_health

                async with request.app.state.sessionmaker() as sess:
                    j = await jobs_health(sess)
                jobs = JobsHealth(**j)
            except Exception as exc:  # noqa: BLE001
                log.debug("health: jobs snapshot unavailable: %s", exc)
        return Health(ok=db_ok, db="up" if db_ok else "down", app_env=s.app_env, jobs=jobs)

    app.include_router(router)

    from fastapi import Depends

    # 2026-10-08 onion/SOLID refactor: former `app/api/*.py` files folded into
    # their owning modules as `<topic>_router.py`; per-module `router.py` is a
    # thin aggregator (lint-imports contracts 1/2 are enforced on those).
    # Each sub-router is mounted here with its own deps because deps differ
    # per route group (user_only vs user_or_cron vs open unsubscribe).
    from app.analysis.ai_router import router as ai_router
    from app.analysis.analysis_router import router as analysis_router
    from app.analysis.lanes_router import router as lanes_router
    from app.analysis.overview_router import router as overview_router
    from app.followups.router import router as followups_router
    from app.identity.auth.deps import current_user, require_user_or_cron
    from app.identity.auth_router import router as auth_router

    # ``require_user_or_cron`` fronts routers that mix user-facing GETs with
    # the two cron-triggered writes (``/crawl/run``, ``/enrichment/auto-send``)
    # so the GitHub Actions workflow (``.github/workflows/daily-crawl.yml``)
    # can still hit those routes with its ``X-Cron-Secret`` header alone. The
    # per-handler ``check_secret`` call inside each cron route remains the
    # authoritative guard for THOSE routes. Everything else — leads, settings,
    # shipper-finder, email, call-list, capacity, enrichment reads — requires
    # a bearer. ``/health`` is standalone. The unsubscribe router stays open
    # (recipients are never logged in).
    # Attach `current_tenant` on every authenticated user route so RLS
    # (`set_config('app.tenant_id', …, true)`) binds for the whole request
    # before any handler query runs. The dep is additive — `current_user`
    # already resolves the principal; `current_tenant` resolves the tenant
    # from that principal and stamps the session.
    #
    # ``user_or_cron`` routes cannot use `current_tenant` as a router dep
    # because `require_user_or_cron` returns None in the cron path. Those
    # handlers (crawl, enrichment auto-send, mail cron, loads refresh)
    # already run under the admin sentinel in `uow_admin()` and bind the
    # tenant inside the service when they need it.
    from app.identity.dependencies import current_tenant
    from app.identity.settings_router import router as settings_router
    from app.inbox.inbox_router import router as inbox_router
    from app.inbox.mail_router import cron_router as mail_cron_router, router as mail_router
    from app.integrations.loads_router import router as loads_router
    from app.outreach.call_list_router import router as call_list_router
    from app.outreach.capacity_router import router as capacity_router
    from app.outreach.email_router import router as email_router
    from app.outreach.unsub_router import unsub_router as unsubscribe_router
    from app.prospecting.brokers_router import router as brokers_router
    from app.prospecting.contacts_router import router as contacts_router
    from app.prospecting.crawl_router import router as crawl_router
    from app.prospecting.enrichment_router import router as enrichment_router
    from app.prospecting.leads_router import router as leads_router
    from app.prospecting.shipper_finder_router import router as shipper_finder_router
    from app.vetting.router import router as vetting_router

    user_or_cron = [Depends(require_user_or_cron)]
    user_only = [Depends(current_user), Depends(current_tenant)]

    app.include_router(auth_router)
    app.include_router(crawl_router, dependencies=user_or_cron)
    app.include_router(leads_router, dependencies=user_only)
    app.include_router(email_router, dependencies=user_only)
    app.include_router(settings_router, dependencies=user_only)
    app.include_router(capacity_router, dependencies=user_only)
    app.include_router(call_list_router, dependencies=user_only)
    app.include_router(brokers_router, dependencies=user_only)
    app.include_router(shipper_finder_router, dependencies=user_only)
    app.include_router(enrichment_router, dependencies=user_or_cron)
    app.include_router(contacts_router, dependencies=user_only)
    app.include_router(ai_router, dependencies=user_only)
    app.include_router(overview_router, dependencies=user_only)
    # Mail + loads connectors (plans 2026-10-01-google-workspace-mail-connector + load-board).
    # Mail is split: owner-only for /status, /test-send, /mailboxes, /disconnect;
    # user-or-cron for /backfill + /incremental (the ingest routes the cron
    # workflow hits). A cron secret must never flip owner-sensitive state like
    # /disconnect or trigger a /test-send on the owner's Google Workspace.
    # ``loads_router`` stays mixed because its /refresh-all is cron-driven and
    # its reads are owner-facing — the per-handler check guards the cron path.
    app.include_router(mail_router, dependencies=user_only)
    app.include_router(inbox_router, dependencies=user_only)
    app.include_router(analysis_router, dependencies=user_only)
    app.include_router(lanes_router, dependencies=user_only)
    app.include_router(mail_cron_router, dependencies=user_or_cron)
    app.include_router(loads_router, dependencies=user_or_cron)
    app.include_router(unsubscribe_router)
    # Jobs API — `/jobs/drain` is cron-callable, `/jobs/{id}` is user-only,
    # `/admin/jobs` is user-only. The drain route also re-checks the cron
    # secret inside its handler so a bare bearer-less request can't trigger
    # a worker tick.
    from app.integrations.jobs_router import admin_router as jobs_admin_router, router as jobs_router

    app.include_router(jobs_router, dependencies=user_or_cron)
    app.include_router(jobs_admin_router, dependencies=user_only)

    # Rates — Lane Rate / Load Profit / Backhaul Finder (plan
    # 2026-10-09-tools-rates-profit-backhaul). All three POSTs read; the
    # EIA weekly refresh runs out of band via `rates.refresh_diesel`.
    from app.rates.router import router as rates_router

    app.include_router(rates_router, dependencies=user_only)

    # Track B tools — Broker Check (vetting) + Follow-ups kanban. Owner-only
    # reads over data we already compute; see
    # projects/ljm-intelligence/plan/2026-10-09-tools-vetting-followups.md.
    app.include_router(vetting_router, dependencies=user_only)
    app.include_router(followups_router, dependencies=user_only)
    return app
