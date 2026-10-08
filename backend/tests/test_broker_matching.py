"""Link-loads-to-brokers: matcher, ingest paths, revenue, migration backfill."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Lead, Load
from app.prospecting.broker_matching import match_broker_lead, normalize_name

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def engine():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _leads(sm):
    async with sm() as s:
        s.add_all([
            Lead(id="L1", kind="Broker", state="NJ", name="Acme Freight LLC", domain="acme-freight.com", primary_email="dispatch@acme-freight.com"),
            Lead(id="L2", kind="Broker", state="NJ", name="Twin Logistics", domain="twin-a.com"),
            Lead(id="L3", kind="Broker", state="NJ", name="Twin Logistics Inc.", domain="twin-b.com"),
            Lead(id="L4", kind="Broker", state="NJ", name="Phone Co", phone="555-0100"),
        ])
        await s.commit()


async def test_normalize_name():
    assert normalize_name("ACME Freight, LLC") == normalize_name("acme freight llc") == "acme freight"


@pytest.mark.parametrize(
    "kw,expected",
    [
        ({"email": "dispatch@acme-freight.com"}, "L1"),
        ({"email": "ops@acme-freight.com"}, "L1"),  # domain
        ({"email": "x@gmail.com"}, None),  # freemail never domain-matches
        ({"name": "ACME Freight, LLC"}, "L1"),
        ({"name": "twin logistics"}, None),  # ambiguous
        ({"phone": "555-0100"}, "L4"),
        ({"email": "a@nowhere.com", "name": "Nobody"}, None),
    ],
)
async def test_matcher_table(sm, kw, expected):
    await _leads(sm)
    async with sm() as s:
        assert await match_broker_lead(s, **kw) == expected


async def test_api_path_sets_link(sm):
    from app.integrations.adapters.loadboard.base import RawLoad
    from app.integrations.loads_service import _store_batch

    await _leads(sm)
    await _store_batch(sm, [
        RawLoad(source="dat", source_ref="a", broker_name="Acme Freight LLC", broker_email="ops@acme-freight.com"),
        RawLoad(source="dat", source_ref="b", broker_name="Unknown Co"),
    ])
    async with sm() as s:
        got = {l.source_ref: l.broker_lead_id for l in (await s.execute(select(Load))).scalars()}
    assert got == {"a": "L1", "b": None}


async def test_inbox_path_sets_link(sm):
    from types import SimpleNamespace
    from datetime import UTC, datetime

    from app.inbox.triage import _upsert_load_from_offer

    await _leads(sm)
    msg = SimpleNamespace(mailbox="m", message_id="1", from_addr="ops@acme-freight.com", sent_at=datetime.now(UTC))
    o = SimpleNamespace(intent="load_offer", broker_name="Acme", lane_from="A, VA", lane_to="B, GA", equipment="van", rate_usd=1000)
    async with sm() as s:
        await _upsert_load_from_offer(s, msg, o)
        await s.commit()
        l = (await s.execute(select(Load))).scalar_one()
    assert l.broker_lead_id == "L1"


async def test_revenue_booked_only(sm):
    from app.prospecting.brokers_service import get_overview_metrics

    await _leads(sm)
    async with sm() as s:
        for i, (st, rate) in enumerate([("booked", 1000), ("booked", 500), ("new", 9000), ("lost", 7000), ("booked", None)]):
            s.add(Load(source="dat", source_ref=f"r{i}", broker_name="x", status=st, rate_usd=rate, broker_lead_id="L1"))
        await s.commit()
    out = await get_overview_metrics(sm, ["L1", "L2"])
    assert out["L1"].revenue_usd == 1500.0
    assert out["L2"].revenue_usd == 0.0


async def test_migration_backfill_only_null_rows(sm, engine):
    path = Path(__file__).parent.parent / "migrations/versions/0029_backfill_load_broker_lead.py"
    spec = importlib.util.spec_from_file_location("m0029", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    await _leads(sm)
    async with sm() as s:
        s.add(Load(source="dat", source_ref="n", broker_name="Acme Freight", status="new"))
        s.add(Load(source="dat", source_ref="k", broker_name="Acme Freight", status="new", broker_lead_id="L4"))
        await s.commit()
    async with engine.begin() as conn:
        assert await conn.run_sync(lambda c: mod.backfill(c)) == 1
        assert await conn.run_sync(lambda c: mod.backfill(c)) == 0  # idempotent
    async with sm() as s:
        got = {l.source_ref: l.broker_lead_id for l in (await s.execute(select(Load))).scalars()}
    assert got == {"n": "L1", "k": "L4"}
