"""LoadStop fleet ingest — upsert on fake ABP pages."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.fleet.models import Truck
from app.integrations.adapters.loadboard.loadstop.fakes import (
    SAMPLE_TRUCKS,
    FakeAbpClient,
)
from app.integrations.adapters.loadboard.loadstop.ingest.fleet import ingest_trucks
from app.shared.orm import LJM_TENANT_ID


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def test_fleet_ingest_inserts_then_upserts_locations(db):
    client = FakeAbpClient({("trucks", "GetAll"): [SAMPLE_TRUCKS]})
    async with db() as s:
        counts = await ingest_trucks(s, client)
        await s.commit()
    assert counts["rows_in"] == 2
    assert counts["rows_upserted"] == 2

    # Second pass with a fresh location string — same unit numbers →
    # upsert, not duplicate; raw envelope refreshes.
    moved = [{**SAMPLE_TRUCKS[0], "locationDescription": "Milwaukee, WI (moving)"}]
    client2 = FakeAbpClient({("trucks", "GetAll"): [moved]})
    async with db() as s:
        await ingest_trucks(s, client2, locations_only=False)
        await s.commit()
    async with db() as s:
        rows = (await s.execute(select(Truck).where(Truck.tenant_id == LJM_TENANT_ID))).scalars().all()
    assert len(rows) == 2  # no duplicate
    t100 = next(r for r in rows if r.unit_number == "T-100")
    assert t100.source == "loadstop"
    assert t100.source_ref == "ls-tr-100"
    assert (t100.raw or {}).get("locationDescription") == "Milwaukee, WI (moving)"
