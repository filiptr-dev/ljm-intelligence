"""Lanes HTTP surface on the PG16 harness (rows seeded from the migration-0036 generator)."""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.deps import UserPrincipal, current_user
from app.db import Base
from app.main import create_app
from tests.analysis.lanes_fixtures import seed

pytestmark = pytest.mark.skipif(
    os.environ.get("TEST_HARNESS") != "pg16" and not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="requires PG16 harness",
)


@pytest.fixture
async def client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)  # redirected to PG by conftest
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    # The PG harness truncates every table between tests, so seed the 0036 demo shape here.
    await seed(maker)
    app = create_app()
    app.state.sessionmaker = maker
    app.dependency_overrides[current_user] = lambda: UserPrincipal(id="u1", email="owner@test", role="owner")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_summary_top_heatmap_runs_shapes(client):
    s = (await client.get("/analysis/lanes/summary", params={"period": "month"})).json()
    assert s["history_runs"] >= 2000 and s["kpis"]["runs"] > 0 and s["statements"]
    t = (await client.get("/analysis/lanes/top", params={"level": "city", "limit": 5})).json()
    assert len(t["lanes"]) == 5
    h = (await client.get("/analysis/lanes/heatmap", params={"period": "year"})).json()
    assert 0 < len(h["arcs"]) <= 50 and h["states"] and h["arcs"][0]["entity"]["text"]
    assert len(h["operating_states"]) == 32 and "TX" not in h["operating_states"] and "LA" in h["operating_states"]
    r = (await client.get("/analysis/lanes/runs", params={"limit": 10})).json()
    assert len(r["items"]) == 10 and r["next_cursor"]
    r2 = (await client.get("/analysis/lanes/runs", params={"limit": 10, "cursor": r["next_cursor"]})).json()
    assert not {i["id"] for i in r["items"]} & {i["id"] for i in r2["items"]}


async def test_filters_and_bad_period(client):
    full = (await client.get("/analysis/lanes/summary", params={"period": "year"})).json()
    tx = (await client.get("/analysis/lanes/summary", params={"period": "year", "state": "TX"})).json()
    lane = (await client.get(
        "/analysis/lanes/summary", params={"period": "year", "lane": "Chicago,IL>Atlanta,GA"})).json()
    assert 0 < lane["kpis"]["runs"] < tx["kpis"]["runs"] < full["kpis"]["runs"]
    assert (await client.get("/analysis/lanes/summary", params={"period": "decade"})).status_code == 422


async def test_ai_without_provider_is_unavailable_not_invented(client, monkeypatch):
    # backend/.env may hold a real Gemini key; never let a test reach the network.
    from app.integrations.adapters.ai import provider as ai_provider

    monkeypatch.setattr(ai_provider, "get_for", lambda *a, **k: None)
    r = (await client.post("/analysis/lanes/ai", params={"period": "month"})).json()
    assert r["status"] == "unavailable" and r["focus_lanes"] == [] and r["ai_error"]
    assert (await client.get("/analysis/lanes/ai", params={"period": "month"})).json() == {}
