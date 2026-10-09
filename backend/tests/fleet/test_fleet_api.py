"""Fleet HTTP surface + the FleetSource port, over the seeded demo fleet."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.deps import UserPrincipal, current_user
from app.db import Base
from app.fleet.adapters.registry import active_source, all_sources
from app.fleet.models import Truck
from app.main import create_app
from tests.analysis.lanes_fixtures import TENANT
from tests.fleet.fleet_fixtures import clear_fleet, seed_fleet


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)  # redirected to PG by conftest
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    await clear_fleet(maker)
    await seed_fleet(maker)
    app = create_app()
    app.state.sessionmaker = maker
    app.dependency_overrides[current_user] = lambda: UserPrincipal(id="u1", email="owner@test", role="owner")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, maker
    await engine.dispose()


async def test_trucks_list_has_20_trucks_8_trailers_and_joined_kpis(env):
    c, _ = env
    r = (await c.get("/fleet/trucks")).json()
    assert r["source"] == "demo" and len(r["trucks"]) == 20 and len(r["trailers"]) == 8
    t = r["trucks"][0]
    assert {"status", "equipment", "driver_name", "miles_30d", "revenue_30d_usd", "cost_per_mile_30d_usd"} <= set(t)
    assert sum(x["runs_30d"] for x in r["trucks"]) > 0
    busy = next(x for x in r["trucks"] if x["runs_30d"] > 0)
    assert busy["miles_30d"] > 0 and busy["revenue_30d_usd"] > 0 and 0.5 < busy["cost_per_mile_30d_usd"] < 4
    assert all(x["runs_30d"] == 0 for x in r["trailers"])
    assert any(x["next_doc_expiry"] for x in r["trucks"])
    assert sum(1 for x in r["trucks"] if x["open_critical_defects"]) >= 2


async def test_alerts_have_something_to_say_in_every_bucket(env):
    c, _ = env
    a = (await c.get("/fleet/alerts")).json()
    assert len(a["expiring_docs"]) >= 1 and len(a["critical_defects"]) >= 1 and len(a["maintenance_due"]) >= 1
    assert all(d["days_left"] <= 14 for d in a["expiring_docs"])
    assert {s["key"] for s in a["statements"]} == {"docs", "defects", "maintenance"}
    docs = next(s for s in a["statements"] if s["key"] == "docs")["text"]
    assert str(len(a["expiring_docs"])) in docs and "expire" in docs


async def test_truck_detail_bundle_and_404(env):
    c, _ = env
    rows = (await c.get("/fleet/trucks")).json()["trucks"]
    unit = max(rows, key=lambda x: x["runs_30d"])
    d = (await c.get(f"/fleet/trucks/{unit['id']}")).json()
    assert d["truck"]["unit_number"] == unit["unit_number"]
    assert d["inspections"] and d["documents"] and d["maintenance"] and d["summary"]
    assert 0 < len(d["recent_runs"]) <= 20
    assert d["kpis"]["runs_count"] > 0 and d["kpis"]["miles"] > 0 and d["kpis"]["dollar_per_mile"] > 0
    run = d["recent_runs"][0]
    assert run["margin_usd"] == pytest.approx(run["revenue_usd"] - run["cost_usd"], abs=0.01)
    assert d["truck"]["last_lat"] is not None
    assert (await c.get("/fleet/trucks/999999")).status_code == 404


async def test_tenant_isolation(env):
    c, maker = env
    async with maker() as s:
        s.add(Truck(tenant_id="01OTHER".ljust(26, "0"), kind="truck", unit_number="X-1", status="available", source="demo"))
        await s.commit()
    r = (await c.get("/fleet/trucks")).json()
    assert len(r["trucks"]) == 20 and "X-1" not in {t["unit_number"] for t in r["trucks"]}  # still just LJM's


async def test_demo_source_satisfies_the_port_and_round_trips(env):
    _, maker = env
    async with maker() as s:
        src = active_source(s)
        assert src.name == "demo" and src.enabled and [x.name for x in all_sources(s)] == ["demo"]
        snap = await src.snapshot(TENANT)
    assert snap.source == "demo" and len(snap.units) == 28
    assert snap.inspections and snap.defects and snap.maintenance and snap.documents
    refs = {u.source_ref for u in snap.units}
    assert {i.unit_ref for i in snap.inspections} <= refs
