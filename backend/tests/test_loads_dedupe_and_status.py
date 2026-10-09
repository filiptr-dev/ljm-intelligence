"""Loads dedupe + take-it status flow (plan 2026-10-08).

Pure service-layer unit tests on PG16 — no AI, no agent sidecar, no
Playwright. Exercises the three new seams:

* ``list_loads_deduped`` collapses two lanes with the same group hash into
  one row with two source badges.
* ``set_group_status`` is transactional across every row in a group.
* The dedupe contact tie-break prefers the row with phone+email.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.integrations.loads_service import (
    _group_hash,
    list_loads_deduped,
    set_group_status,
)
from app.models import Load

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


async def _seed_pair(sm) -> str:
    """Seed two rows with the same group hash, different sources."""
    posted_a = datetime.now(UTC)
    posted_b = posted_a - timedelta(hours=1)
    pickup = datetime.now(UTC) + timedelta(days=1)
    from app.integrations.adapters.loadboard.base import RawLoad

    raw = RawLoad(
        source="dat",
        source_ref="dedupe-A",
        broker_name="Acme Brokers",
        broker_email=None,
        broker_phone="555-100-0001",
        origin_state="VA",
        dest_state="GA",
        pickup_date=pickup,
        equipment="van",
    )
    gh = _group_hash(raw)
    async with sm() as s:
        s.add(Load(
            source="dat", source_ref="dedupe-A", broker_name="Acme Brokers",
            broker_phone="555-100-0001", broker_email=None,
            origin_state="VA", dest_state="GA", pickup_date=pickup,
            equipment="van", rate_usd=1800, miles=500, posted_at=posted_b,
            raw={}, status="new", dedupe_group_hash=gh,
        ))
        s.add(Load(
            source="inbox", source_ref="msg:1", broker_name="Acme Brokers",
            broker_phone="555-100-0002", broker_email="dispatch@acme.com",
            origin_state="VA", dest_state="GA", pickup_date=pickup,
            equipment="van", rate_usd=1700, miles=500, posted_at=posted_a,
            raw={}, status="new", dedupe_group_hash=gh,
        ))
        await s.commit()
    return gh


async def test_list_loads_deduped_collapses_matching_lane(sm):
    gh = await _seed_pair(sm)
    async with sm() as s:
        groups = await list_loads_deduped(s, limit=50)
    row = next((g for g in groups if g.group_hash == gh), None)
    assert row is not None, "group not found"
    kinds = {src["kind"] for src in row.sources}
    assert kinds == {"dat", "inbox"}, kinds
    # Rate drops to the lower of the two (1700).
    assert row.rate_usd == 1700
    # Tie-break: the inbox row (phone + email) wins contact.
    assert row.broker["email"] == "dispatch@acme.com"
    assert row.broker["phone"] == "555-100-0002"


async def test_set_group_status_updates_every_row_in_group(sm):
    gh = await _seed_pair(sm)
    rows_updated = await set_group_status(sm, gh, "contacted")
    assert rows_updated == 2
    async with sm() as s:
        from sqlalchemy import select

        rows = (await s.execute(
            select(Load).where(Load.dedupe_group_hash == gh)
        )).scalars().all()
    assert all(r.status == "contacted" for r in rows)
    assert all(r.status_at is not None for r in rows)


async def test_set_group_status_booked_removes_from_default_list(sm):
    gh = await _seed_pair(sm)
    await set_group_status(sm, gh, "booked")
    async with sm() as s:
        groups = await list_loads_deduped(s, limit=50)
    assert all(g.group_hash != gh for g in groups), "booked group leaked into default list"
