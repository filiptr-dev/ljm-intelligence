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


class Health(BaseModel):
    ok: bool
    db: Literal["up", "down"]
    app_env: str


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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="LJM Intelligence API",
        version="0.1.0",
        summary="Real lead crawler + Gemini discovery/scoring for LJM International.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings

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
    async def health(request: Request) -> Health:
        """Readiness: process + database. Cold Neon wakes are tolerated by db.ping() (one retry) and
        the generous `db_health_timeout`. Never 500 — return `db:'down'` so the frontend can decide."""
        s: Settings = request.app.state.settings
        db_ok = False
        try:
            async with asyncio.timeout(s.db_health_timeout):
                await ping(request.app.state.engine)
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("health: db unreachable: %s", exc)
        return Health(ok=db_ok, db="up" if db_ok else "down", app_env=s.app_env)

    app.include_router(router)

    from fastapi import Depends

    from app.api.ai import router as ai_router
    from app.api.auth import router as auth_router
    from app.api.call_list import router as call_list_router
    from app.api.capacity import router as capacity_router
    from app.api.crawl import router as crawl_router
    from app.api.email import router as email_router
    from app.api.enrichment import router as enrichment_router
    from app.api.enrichment import unsub_router as unsubscribe_router
    from app.api.leads import router as leads_router
    from app.api.loads import router as loads_router
    from app.api.mail import router as mail_router
    from app.api.overview import router as overview_router
    from app.api.settings import router as settings_router
    from app.api.shipper_finder import router as shipper_finder_router
    from app.auth.deps import current_user, require_user_or_cron

    # ``require_user_or_cron`` fronts routers that mix user-facing GETs with
    # the two cron-triggered writes (``/crawl/run``, ``/enrichment/auto-send``)
    # so the GitHub Actions workflow (``.github/workflows/daily-crawl.yml``)
    # can still hit those routes with its ``X-Cron-Secret`` header alone. The
    # per-handler ``check_secret`` call inside each cron route remains the
    # authoritative guard for THOSE routes. Everything else — leads, settings,
    # shipper-finder, email, call-list, capacity, enrichment reads — requires
    # a bearer. ``/health`` is standalone. The unsubscribe router stays open
    # (recipients are never logged in).
    user_or_cron = [Depends(require_user_or_cron)]
    user_only = [Depends(current_user)]

    app.include_router(auth_router)
    app.include_router(crawl_router, dependencies=user_or_cron)
    app.include_router(leads_router, dependencies=user_only)
    app.include_router(email_router, dependencies=user_only)
    app.include_router(settings_router, dependencies=user_only)
    app.include_router(capacity_router, dependencies=user_only)
    app.include_router(call_list_router, dependencies=user_only)
    app.include_router(shipper_finder_router, dependencies=user_only)
    app.include_router(enrichment_router, dependencies=user_or_cron)
    app.include_router(ai_router, dependencies=user_only)
    app.include_router(overview_router, dependencies=user_only)
    # Mail + loads connectors (plans 2026-10-01-google-workspace-mail-connector + load-board).
    # ``user_or_cron`` so cron-secret headers can hit backfill/incremental/refresh-all.
    app.include_router(mail_router, dependencies=user_or_cron)
    app.include_router(loads_router, dependencies=user_or_cron)
    app.include_router(unsubscribe_router)
    return app
