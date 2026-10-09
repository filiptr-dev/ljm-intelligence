"""Migration 0039: every demo run stays inside the operating area; the shift story survives; PG round-trip."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis import lanes_history_service as H
from app.analysis.models import FreightRun
from app.analysis.operating_area import OPERATING_STATE_SET, OPERATING_STATES
from app.db import Base
from app.fleet import models as _fleet_models  # noqa: F401  (registers trucks for the FK)
from app.rates.domain import CityState, haversine_miles, highway_miles, resolve_city
from tests.analysis.lanes_fixtures import NOW, TENANT

_FILE = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0039_demo_operating_area.py"


def load_0039():
    spec = importlib.util.spec_from_file_location("mig0039", _FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mig = load_0039()


def _resolve(city: str, state: str) -> tuple[float, float]:
    p = resolve_city(CityState(city, state))
    return p.lat, p.lon


def rows():
    return mig.generate_runs(NOW, lambda s, d: None, _resolve,
                             lambda a, b, c, d: highway_miles(haversine_miles(a, b, c, d)))


def test_operating_states_is_the_32_east_of_texas():
    assert len(OPERATING_STATES) == 32 == len(OPERATING_STATE_SET)
    assert not OPERATING_STATE_SET & {"TX", "OK", "KS", "NE", "SD", "ND", "NM", "CO", "AZ", "CA", "WA"}
    assert {"LA", "AR", "MO", "IA", "MN", "DC"} <= OPERATING_STATE_SET


def test_every_demo_run_origin_and_destination_is_in_the_operating_area():
    data = rows()
    assert len(data) == 2000 and data == rows()
    bad = {(r["origin_state"], r["dest_state"]) for r in data
           if r["origin_state"] not in OPERATING_STATE_SET or r["dest_state"] not in OPERATING_STATE_SET}
    assert not bad, bad
    assert {r["source"] for r in data} == {"demo"} and {r["tenant_id"] for r in data} == {TENANT}


def test_new_homes_are_in_the_area():
    assert all(state in OPERATING_STATE_SET for _, state, _, _ in mig._NEW_HOMES.values())


@pytest.fixture
async def top():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(e, expire_on_commit=False)
    async with sm() as s:
        s.add_all([FreightRun(**r) for r in rows()])
        await s.commit()
    out = {}
    for period in ("month", "year"):
        async with sm() as s:
            t = await H.top_lanes(s, H.Scope(tenant_id=TENANT), period, "city", 60, NOW)
        out[period] = {ln.key: ln for ln in t.lanes}
    yield out
    await e.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("period", ["month", "year"])
async def test_one_lane_declines_two_rise(top, period):
    lanes = top[period]
    assert lanes["New Orleans,LA>Memphis,TN"].trend_pct == pytest.approx(-11, abs=3)
    assert lanes["Chicago,IL>Atlanta,GA"].trend_pct == pytest.approx(8, abs=3)
    assert (lanes["Jacksonville,FL>Chicago,IL"].trend_pct or 0) >= 6


pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="set DATABASE_URL_TEST_PG=postgresql+psycopg://... for the PG round-trip",
)


@pg
def test_pg_up_down_up_keeps_demo_inside_the_area():
    import psycopg
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL_TEST_PG"]  # passed via cfg.attributes: env.py prefers settings (maybe prod)
    sync = url.replace("postgresql+psycopg://", "postgresql://")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    area = list(OPERATING_STATES)

    def one(sql: str, *args) -> int:
        with psycopg.connect(sync) as c, c.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()[0]

    def outside_runs() -> int:
        return one("SELECT count(*) FROM freight_runs WHERE source='demo' AND NOT "
                   "(origin_state = ANY(%s) AND dest_state = ANY(%s))", area, area)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert one("SELECT count(*) FROM freight_runs WHERE source='demo'") == 2000
    assert outside_runs() == 0
    assert one("SELECT count(*) FROM trucks WHERE source='demo' AND NOT (home_base_state = ANY(%s))", area) == 0
    assert one("SELECT count(*) FROM freight_runs WHERE source='demo' AND truck_id IS NOT NULL") > 1000
    assert one("SELECT count(*) FROM trucks WHERE last_lat IS NULL") == 0

    with psycopg.connect(sync, autocommit=True) as c, c.cursor() as cur:
        cur.execute("INSERT INTO lane_insights_cache (tenant_id, period_key, input_hash) VALUES (%s,'x','h')", (TENANT,))
    command.downgrade(cfg, "0038")  # back to the 0037 data shape: still valid, still linked
    assert one("SELECT count(*) FROM freight_runs WHERE source='demo'") == 2000
    assert outside_runs() > 0
    assert one("SELECT count(*) FROM freight_runs WHERE source='demo' AND truck_id IS NOT NULL") > 1000
    assert one("SELECT count(*) FROM trucks WHERE source='demo'") == 28
    assert one("SELECT count(*) FROM trucks WHERE last_lat IS NULL") == 0
    command.upgrade(cfg, "head")
    assert outside_runs() == 0
    assert one("SELECT count(*) FROM lane_insights_cache WHERE tenant_id = %s", TENANT) == 0
