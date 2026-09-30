"""POST /crawl/run must reject requests without the shared cron secret when one is set.

Isolation notes (added when Slice 2b flipped ``osm_overpass_enabled`` to True by default):

* We hand-wire an in-memory SQLite ``sessionmaker`` onto ``app.state`` and SKIP
  the real lifespan. Otherwise ``create_app()`` would open a Neon pool using
  the DATABASE_URL from ``backend/.env`` — production — and the intake
  ``CrawlRun`` row plus the background ``run_crawl`` writes would land in
  prod.
* ``osm_overpass_enabled=False`` on the settings override, plus a defensive
  patch on ``fetch_overpass_elements``, keeps the background pipeline fully
  offline. FastAPI's ``BackgroundTasks`` runs before the ASGI response is
  finished being delivered, so a live Overpass fetch here doesn't just leak
  — it hangs the test until Overpass times out.
"""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import Base
from app.main import create_app


def _mk_settings(**overrides) -> Settings:
    from pydantic import SecretStr

    base = Settings()
    return base.model_copy(
        update={
            "cron_secret": SecretStr("s3cret"),
            "gemini_api_key": None,  # skip the gemini stage in the pipeline
            "osm_overpass_enabled": False,  # keep the background pipeline offline
            "osm_overpass_states": [],
            "osm_overpass_max_states": 0,
            **overrides,
        }
    )


async def _sqlite_sessionmaker() -> async_sessionmaker:
    """Fresh in-memory SQLite with the full schema — lets the background
    ``run_crawl`` write its CrawlRun rows somewhere real without touching Neon.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_crawl_run_rejects_wrong_secret() -> None:
    app = create_app(_mk_settings())
    app.state.sessionmaker = await _sqlite_sessionmaker()  # skip Neon lifespan
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/crawl/run", headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_crawl_run_accepts_correct_secret_and_returns_202() -> None:
    app = create_app(_mk_settings())
    app.state.sessionmaker = await _sqlite_sessionmaker()

    # No FMCSA call, no Gemini, no Overpass — just prove the auth + 202 path.
    async def fake_fetch(**_):
        return []

    async def fake_overpass(*_a, **_kw):  # belt-and-suspenders; enabled=False already skips
        return []

    with (
        patch("app.pipeline.run.fetch_fmcsa", side_effect=fake_fetch),
        patch("app.pipeline.run.fetch_overpass_elements", side_effect=fake_overpass),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/crawl/run?limit=5",
                headers={"X-Cron-Secret": "s3cret"},
            )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["run_id"].startswith("run_")
