"""Loads ingest — east-of-Texas + dry-van filter; dedupe on (source, source_ref)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.integrations.adapters.loadboard.loadstop.fakes import FakeAbpClient
from app.integrations.adapters.loadboard.loadstop.ingest.loads import (
    _is_dry_van,
    ingest_loads,
)
from app.prospecting.models import Load

SAMPLE_LOADS = [
    # Mirror the splitLoads/GetAllWithSP grid shape:
    # vanType=1 is Dry Van, totalAmount is the rate, calculatedTotalMiles
    # is the mileage, pickup/deliveryState carry the lanes.
    {"id": "L1", "pickupState": "GA", "deliveryState": "NC", "vanType": 1, "totalAmount": 1800, "calculatedTotalMiles": 420, "customerName": "Acme", "pickupCity": "Atlanta", "deliveryCity": "Charlotte"},
    {"id": "L2", "pickupState": "TX", "deliveryState": "OH", "vanType": 1, "totalAmount": 2200, "calculatedTotalMiles": 1400, "customerName": "TX Broker"},  # OUT: TX origin
    {"id": "L3", "pickupState": "OH", "deliveryState": "PA", "equipment": "Flatbed", "totalAmount": 1500, "calculatedTotalMiles": 300, "customerName": "Flat Co"},  # OUT: not dry van
    {"id": "L4", "pickupState": "FL", "deliveryState": "GA", "vanType": 1, "totalAmount": 900, "calculatedTotalMiles": 300, "customerName": "FL Co"},
]


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def test_dry_van_predicate_classifies_known_equipments():
    assert _is_dry_van("Van")
    assert _is_dry_van("Dry Van")
    assert _is_dry_van("DV")
    assert not _is_dry_van("Reefer")
    assert not _is_dry_van("Flatbed")
    assert not _is_dry_van("Step Deck")
    assert not _is_dry_van(None)


async def test_loads_filters_texas_and_non_van(db):
    client = FakeAbpClient({("splitLoads", "GetAllWithSP"): [SAMPLE_LOADS]})
    async with db() as s:
        counts = await ingest_loads(s, client)
        await s.commit()
    # Only L1 and L4 clear both filters.
    assert counts["rows_upserted"] == 2
    async with db() as s:
        rows = (await s.execute(select(Load).where(Load.source == "loadstop"))).scalars().all()
    assert sorted(r.source_ref for r in rows) == ["split:L1", "split:L4"]
    # Dedupe: a second ingest with same ids must be an upsert, not duplicate.
    client2 = FakeAbpClient({("splitLoads", "GetAllWithSP"): [SAMPLE_LOADS]})
    async with db() as s:
        await ingest_loads(s, client2)
        await s.commit()
    async with db() as s:
        rows = (await s.execute(select(Load).where(Load.source == "loadstop"))).scalars().all()
    assert len(rows) == 2
