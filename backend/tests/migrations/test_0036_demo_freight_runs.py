"""Migration 0036: the deterministic demo seeder (pure) + a Postgres round-trip (opt-in)."""

from __future__ import annotations

import os
from collections import Counter

import pytest

from tests.analysis.lanes_fixtures import NOW, TENANT, demo_rows

DOMINANT = {"chi-atl", "dal-mem", "cmh-clt", "lax-phx", "hbg-jax"}


def _rpm(rows):
    return sum(float(r["revenue_usd"]) for r in rows) / sum(r["miles"] for r in rows)


def test_count_deterministic_and_all_demo_ljm():
    a, b = demo_rows(), demo_rows()
    assert len(a) == 2000 and a == b
    assert {r["source"] for r in a} == {"demo"} and {r["tenant_id"] for r in a} == {TENANT}
    assert all(r["raw"]["demo"] is True for r in a)
    span = (NOW - min(r["pickup_at"] for r in a)).days
    assert 700 <= span <= 735 and max(r["pickup_at"] for r in a) <= NOW


def test_dominant_lanes_carry_about_60_percent():
    g = Counter(r["raw"]["lane_group"] for r in demo_rows())
    assert 0.55 <= sum(g[k] for k in DOMINANT) / 2000 <= 0.65


def test_market_shift_is_baked_in():
    rows = demo_rows()

    def grp(name, arc):
        return [r for r in rows if r["raw"]["lane_group"] == name and r["raw"]["shift_arc"] == arc]

    assert _rpm(grp("dal-mem", "post")) / _rpm(grp("dal-mem", "pre")) == pytest.approx(0.89, abs=0.03)
    assert _rpm(grp("chi-atl", "post")) / _rpm(grp("chi-atl", "pre")) == pytest.approx(1.08, abs=0.03)
    assert not grp("lrd-chi", "pre") and len(grp("lrd-chi", "post")) > 30
    # Laredo ramps up: more runs in the newest 4 months than in the 4 before
    post = grp("lrd-chi", "post")
    newest = sum(1 for r in post if (NOW - r["pickup_at"]).days < 120)
    assert newest > len(post) - newest


def test_seasonality_and_costs_are_sane():
    rows = demo_rows()
    reefer = Counter(r["pickup_at"].month for r in rows if r["equipment"] == "reefer")
    summer = sum(reefer[m] for m in (5, 6, 7, 8)) / 4
    rest = sum(v for m, v in reefer.items() if m not in (5, 6, 7, 8)) / 8
    assert summer > rest * 1.1
    for r in rows[:200]:
        assert r["cost_dispatch_usd"] >= 75 and r["cost_fuel_usd"] > 0 and r["delivery_at"] > r["pickup_at"]
    m = sum(float(r["revenue_usd"]) - sum(float(r[k]) for k in
            ("cost_fuel_usd", "cost_driver_usd", "cost_load_usd", "cost_dispatch_usd")) for r in rows)
    assert m > 0


def test_length_band_mix_roughly_45_40_15():
    miles = [r["miles"] for r in demo_rows()]
    short = sum(1 for m in miles if m < 500) / 2000
    long_ = sum(1 for m in miles if m > 900) / 2000
    assert 0.35 <= short <= 0.5 and 0.10 <= long_ <= 0.2


pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="set DATABASE_URL_TEST_PG=postgresql+psycopg://... for the PG round-trip",
)


@pg
def test_pg_upgrade_idempotent_and_downgrade_removes_only_demo_rows():
    import psycopg
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL_TEST_PG"]
    sync = url.replace("postgresql+psycopg://", "postgresql://")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False

    def count(where: str) -> int:
        with psycopg.connect(sync) as c, c.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM freight_runs WHERE {where}")
            return cur.fetchone()[0]

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert count("source = 'demo'") == 2000
    command.upgrade(cfg, "head")  # no-op
    assert count("source = 'demo'") == 2000

    with psycopg.connect(sync, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO freight_runs (tenant_id, source, origin_city, origin_state, dest_city, dest_state,"
            " origin_lat, origin_lng, dest_lat, dest_lng, miles, pickup_at, delivery_at, revenue_usd,"
            " cost_fuel_usd, cost_driver_usd, cost_load_usd, cost_dispatch_usd) VALUES"
            " (%s,'real','A','TX','B','TX',1,1,1,1,100,now(),now(),1,1,1,1,1)", (TENANT,),
        )
    command.downgrade(cfg, "0035")
    assert count("source = 'real'") == 1 and count("source = 'demo'") == 0
    command.upgrade(cfg, "head")
    assert count("source = 'demo'") == 2000 and count("source = 'real'") == 1
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
