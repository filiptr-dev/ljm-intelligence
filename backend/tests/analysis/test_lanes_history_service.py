"""Lanes history aggregates: periods, top lanes, length bands, heat map, runs paging."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis import lanes_history_service as svc
from app.analysis.models import FreightRun
from app.db import Base
from tests.analysis.lanes_fixtures import NOW, TENANT, seed

pytestmark = pytest.mark.asyncio

SCOPE = svc.Scope(tenant_id=TENANT)


@pytest.fixture
async def sm():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(e, expire_on_commit=False)
    await seed(maker)
    yield maker
    await e.dispose()


async def test_summary_month_buckets_add_up_to_kpis(sm):
    async with sm() as s:
        r = await svc.summary(s, SCOPE, "month", NOW)
    assert r.history_runs == 2000
    assert r.kpis.runs == sum(b.metrics.runs for b in r.buckets) > 700
    assert len(r.buckets) in (12, 13)
    assert abs(sum(c.pct for c in r.cost_split) - 100) < 0.5
    assert r.kpis.margin == pytest.approx(r.kpis.revenue - r.kpis.cost_total, abs=0.5)
    assert 0 < (r.kpis.margin_pct or 0) < 60


async def test_summary_week_and_year_windows(sm):
    async with sm() as s:
        wk = await svc.summary(s, SCOPE, "week", NOW)
        yr = await svc.summary(s, SCOPE, "year", NOW)
    assert 0 < wk.kpis.runs < yr.kpis.runs <= 2000
    assert len(yr.buckets) == 3  # 2024/2025/2026 calendar years in a 24 month window
    assert all(b.label.isdigit() for b in yr.buckets)


async def test_every_requested_statement_is_present_in_plain_text(sm):
    async with sm() as s:
        r = await svc.summary(s, SCOPE, "month", NOW)
    keys = {x.key for x in r.statements}
    assert {"totals", "cost_split", "most_frequent", "most_miles", "length_band", "shift"} <= keys
    totals = next(x for x in r.statements if x.key == "totals").text
    assert "runs a week" in totals and "a month" in totals and "a year" in totals
    assert "Fuel" in next(x for x in r.statements if x.key == "cost_split").text or "fuel" in next(
        x for x in r.statements if x.key == "cost_split").text
    assert "Chicago" in next(x for x in r.statements if x.key == "most_frequent").text


async def test_market_shift_is_detected_deterministically(sm):
    async with sm() as s:
        yr = await svc.summary(s, SCOPE, "year", NOW)
    assert yr.shifting is True
    text = next(x for x in yr.statements if x.key == "shift").text
    assert text.startswith("Yes")
    assert "Laredo" in text  # the nearshoring lane only exists in the recent half


async def test_top_lanes_state_and_city(sm):
    async with sm() as s:
        st = await svc.top_lanes(s, SCOPE, "year", "state", 5, NOW)
        ct = await svc.top_lanes(s, SCOPE, "year", "city", 5, NOW)
    assert len(st.lanes) == 5 and st.lanes[0].runs >= st.lanes[-1].runs
    assert ct.lanes[0].origin == "Chicago, IL" and ct.lanes[0].dest == "Atlanta, GA"
    assert ct.lanes[0].trend_pct is not None and ct.lanes[0].trend_pct > 3  # rate/mi up in the recent half
    dal = next(x for x in (await _all_city(sm)).lanes if x.origin == "Dallas, TX")
    assert dal.trend_pct is not None and dal.trend_pct < -3 and (dal.runs_trend_pct or 0) < -15


async def _all_city(sm):
    async with sm() as s:
        return await svc.top_lanes(s, SCOPE, "year", "city", 50, NOW)


async def test_length_bands_cover_all_runs(sm):
    async with sm() as s:
        r = await svc.summary(s, SCOPE, "year", NOW)
    assert [b.band for b in r.length_bands] == ["0–250", "250–500", "500–750", "750–1000", "1000+"]
    assert sum(b.runs for b in r.length_bands) == r.kpis.runs
    assert abs(sum(b.pct for b in r.length_bands) - 100) < 0.6


async def test_heatmap_caps_and_entities(sm):
    async with sm() as s:
        h = await svc.heatmap(s, SCOPE, "year", NOW)
    assert 0 < len(h.arcs) <= 50 and len(h.origins) <= 1000
    arc = h.arcs[0]
    assert arc.entity.kind == "lane" and arc.entity.key.startswith("lane:") and arc.entity.text
    assert arc.entity.metrics.cost_fuel > 0 and arc.entity.dominant_band
    tx = next(e for e in h.states if e.key == "state:TX")
    assert tx.metrics.runs > 0 and tx.runs_as_origin and tx.runs_as_dest and "TX" in tx.text
    assert all(c.key.startswith("city:") for c in h.cities) and h.cities[0].lat != 0
    assert {p.key for p in h.origins} <= {c.key for c in h.cities}


async def test_state_and_lane_filters_narrow_everything(sm):
    async with sm() as s:
        full = await svc.summary(s, SCOPE, "year", NOW)
        tx = await svc.summary(s, svc.Scope(TENANT, state="tx"), "year", NOW)
        lane = await svc.summary(s, svc.Scope(TENANT, lane="Chicago,IL>Atlanta,GA"), "year", NOW)
        st_lane = await svc.summary(s, svc.Scope(TENANT, lane="IL>GA"), "year", NOW)
        expect = (await s.execute(
            select(func.count()).select_from(FreightRun).where(
                FreightRun.origin_city == "Chicago", FreightRun.dest_city == "Atlanta"))).scalar()
    assert 0 < tx.kpis.runs < full.kpis.runs
    assert lane.kpis.runs == expect and st_lane.kpis.runs >= expect


async def test_runs_page_keyset_has_no_overlap(sm):
    async with sm() as s:
        p1 = await svc.runs_page(s, SCOPE, "year", None, 50, NOW)
        p2 = await svc.runs_page(s, SCOPE, "year", p1.next_cursor, 50, NOW)
    assert len(p1.items) == 50 and p1.next_cursor and p1.total > 100
    assert not {r.id for r in p1.items} & {r.id for r in p2.items}
    assert p1.items[-1].pickup_at >= p2.items[0].pickup_at
    assert all(abs(r.margin - (r.revenue - r.cost_fuel - r.cost_driver - r.cost_load - r.cost_dispatch)) < 0.02
               for r in p1.items)


async def test_other_tenant_sees_nothing(sm):
    async with sm() as s:
        r = await svc.summary(s, svc.Scope(tenant_id="01OTHERTENANT0000000000000A"), "month", NOW)
    assert r.kpis.runs == 0 and r.statements[0].key == "empty" and r.buckets == []
