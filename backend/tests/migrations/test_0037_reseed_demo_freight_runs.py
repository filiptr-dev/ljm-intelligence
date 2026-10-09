"""Migration 0037: the improved demo seeder (pure, through the real history service) + a PG round-trip."""

from __future__ import annotations

import os
from collections import Counter

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis import lanes_history_service as H
from app.db import Base
from tests.analysis.lanes_fixtures import NOW, TENANT, demo_rows, seed

DOMINANT = {"chi-atl", "dal-mem", "cmh-clt", "lax-phx", "hbg-jax"}
DAL, CHI, LRD = "Dallas,TX>Memphis,TN", "Chicago,IL>Atlanta,GA", "Laredo,TX>Chicago,IL"


def test_deterministic_demo_only_ljm_and_distinct_from_0036():
    a, b = demo_rows(), demo_rows()
    assert len(a) == 2000 and a == b
    assert {r["source"] for r in a} == {"demo"} and {r["tenant_id"] for r in a} == {TENANT}
    assert a != demo_rows(rev="0036")
    g = Counter(r["raw"]["lane_group"] for r in a)
    assert 0.5 <= sum(g[k] for k in DOMINANT) / 2000 <= 0.65


@pytest.fixture
async def top():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(e, expire_on_commit=False)
    await seed(sm)
    out = {}
    for period in ("month", "year"):
        async with sm() as s:
            t = await H.top_lanes(s, H.Scope(tenant_id=TENANT), period, "city", 60, NOW)
        out[period] = {ln.key: ln for ln in t.lanes}
    yield out
    await e.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("period", ["month", "year"])
async def test_market_shift_is_visible_in_month_and_year(top, period):
    lanes = top[period]
    assert lanes[DAL].trend_pct == pytest.approx(-11, abs=3)
    assert lanes[CHI].trend_pct == pytest.approx(8, abs=3)
    assert lanes[LRD].trend_pct is not None and lanes[LRD].trend_pct >= 6  # has a baseline and a clear rise
    assert (lanes[LRD].runs_trend_pct or 0) > 20
    assert (lanes[DAL].runs_trend_pct or 0) < -5


pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="set DATABASE_URL_TEST_PG=postgresql+psycopg://... for the PG round-trip",
)


@pg
def test_pg_0037_reseeds_demo_only_clears_cache_and_downgrade_restores_0036_shape():
    import psycopg
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL_TEST_PG"]
    sync = url.replace("postgresql+psycopg://", "postgresql://")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False

    def scalar(sql: str):
        with psycopg.connect(sync) as c, c.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()[0]

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0036")
    old_mem = scalar("SELECT count(*) FROM freight_runs WHERE raw->>'lane_group'='lrd-chi' AND raw->>'shift_arc'='pre'")
    assert old_mem == 0  # 0036: Laredo has no baseline

    with psycopg.connect(sync, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO freight_runs (tenant_id, source, origin_city, origin_state, dest_city, dest_state,"
            " origin_lat, origin_lng, dest_lat, dest_lng, miles, pickup_at, delivery_at, revenue_usd,"
            " cost_fuel_usd, cost_driver_usd, cost_load_usd, cost_dispatch_usd) VALUES"
            " (%s,'real','A','TX','B','TX',1,1,1,1,100,now(),now(),1,1,1,1,1)", (TENANT,),
        )
        cur.execute(
            "INSERT INTO lane_insights_cache (tenant_id, period_key, input_hash, model, focus_lanes,"
            " declining_lanes, market_shifts, cost_levers, entity_insights) VALUES"
            " (%s,'year||','h','m','[]','[]','[]','[]','{}')", (TENANT,),
        )

    command.upgrade(cfg, "0037")  # 0037 is the unit under test; later revisions reseed again
    assert scalar("SELECT count(*) FROM freight_runs WHERE source='demo'") == 2000
    assert scalar("SELECT count(*) FROM freight_runs WHERE source='real'") == 1
    assert scalar("SELECT count(*) FROM lane_insights_cache") == 0
    assert scalar("SELECT count(*) FROM freight_runs WHERE raw->>'lane_group'='lrd-chi' AND raw->>'shift_arc'='pre'") > 10

    command.downgrade(cfg, "0036")
    assert scalar("SELECT count(*) FROM freight_runs WHERE source='demo'") == 2000
    assert scalar("SELECT count(*) FROM freight_runs WHERE source='real'") == 1
    assert scalar("SELECT count(*) FROM freight_runs WHERE raw->>'lane_group'='lrd-chi' AND raw->>'shift_arc'='pre'") == 0
    command.upgrade(cfg, "0037")  # 0037 is the unit under test; later revisions reseed again
    assert scalar("SELECT count(*) FROM freight_runs WHERE source='demo'") == 2000
