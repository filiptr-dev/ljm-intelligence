"""Lane rates ingest — upsert on (tenant, source, source_ref)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.integrations.adapters.loadboard.loadstop.fakes import FakeAbpClient
from app.integrations.adapters.loadboard.loadstop.ingest.rates import ingest_rates
from app.rates.models import LaneRate
from app.shared.orm import LJM_TENANT_ID

SAMPLE_RATES = [
    {"id": "lr-1", "originState": "GA", "destState": "NC", "equipment": "Van", "asOfMonth": "2026-10", "low": 1.80, "avg": 2.10, "high": 2.40, "miles": 420},
    {"id": "lr-2", "originState": "FL", "destState": "NY", "equipment": "Van", "asOfMonth": "2026-10", "low": 2.0, "avg": 2.3, "high": 2.6, "miles": 1200},
]


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def test_rates_upsert_updates_in_place(db):
    client = FakeAbpClient({("globalLaneRates", "GetAllWithSP"): [SAMPLE_RATES]})
    async with db() as s:
        counts = await ingest_rates(s, client)
        await s.commit()
    assert counts["rows_upserted"] == 2

    bumped = [{**SAMPLE_RATES[0], "avg": 2.25}]
    client2 = FakeAbpClient({("globalLaneRates", "GetAllWithSP"): [bumped]})
    async with db() as s:
        await ingest_rates(s, client2)
        await s.commit()
    async with db() as s:
        rows = (await s.execute(select(LaneRate).where(LaneRate.tenant_id == LJM_TENANT_ID))).scalars().all()
    assert len(rows) == 2  # no duplicate
    hit = next(r for r in rows if r.source_ref == "lr-1")
    assert float(hit.avg) == 2.25
