"""FMCSA re-publish merge — a DOT reissued under a new MC must land on the
existing row, not trigger a UniqueViolation.

Scenario reproducing prod error "duplicate key value violates unique constraint
leads_dot_key" (every crawl since 2026-09-30): a prior run stored the row with
``id=DOT-6315665``, later FMCSA emits the same DOT with an MC so our generated
id becomes ``MC-78836882``. The old plain ``on_conflict (id)`` insert aborted
the whole page transaction; the pre-check in ``_upsert_fmcsa_page`` now
re-points the upsert onto the existing row.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.integrations.adapters.enrichment.fmcsa import DiscoveredLead
from app.models import Lead, LeadSource
from app.prospecting.pipeline.run import _upsert_fmcsa_page


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_republished_dot_merges_onto_existing_row(sm):
    started = datetime(2026, 10, 9, 10, 6, tzinfo=UTC)

    # 1) first-run shape: the row was first ingested with id=DOT-6315665 (no MC).
    async with sm() as s:
        s.add(
            Lead(
                id="DOT-6315665",
                mc=None,
                dot="6315665",
                name="RAIL AND PLANT SERVICES CO",
                kind="Broker",
                state="LA",
                first_seen_at=started,
                last_seen_at=started,
                first_seen_run_id="run_old",
                last_seen_run_id="run_old",
                raw={"fmcsa": {"dot_number": "6315665"}},
            )
        )
        await s.commit()

    # 2) today's crawl re-emits the same DOT, this time with an MC — our id
    #    generator now says MC-78836882. Before the fix this blew up with
    #    UniqueViolation on leads_dot_key; after the fix the pre-check finds
    #    the existing DOT-6315665 and merges onto it.
    republished = DiscoveredLead(
        id="MC-78836882",
        mc="78836882",
        dot="6315665",
        domain=None,
        name="RAIL AND PLANT SERVICES CO",
        kind="Broker",
        state="LA",
        city="SPRINGFIELD",
        address="17141 CARTHAGE BLUFF RD.",
        phone="2253158452",
        primary_email="amanda.rapsco@gmail.com",
        raw={"dot_number": "6315665", "add_date": "20260101", "legal_name": "RAIL AND PLANT SERVICES CO"},
    )

    new_ids, _min_add_date = await _upsert_fmcsa_page(
        sm, [republished], run_id="run_new", started=started
    )

    # No new row created — the republished DOT merged onto the existing one.
    assert new_ids == []

    async with sm() as s:
        rows = (await s.execute(select(Lead).where(Lead.dot == "6315665"))).scalars().all()
        assert len(rows) == 1, "republished DOT must not create a sibling row"
        row = rows[0]
        assert row.id == "DOT-6315665", "existing id is the stable one; keep it"
        assert row.mc == "78836882", "mc should backfill when previously NULL"
        assert row.last_seen_run_id == "run_new"
        assert row.primary_email == "amanda.rapsco@gmail.com"

        # LeadSource should land under the existing lead_id.
        sources = (
            await s.execute(select(LeadSource).where(LeadSource.lead_id == "DOT-6315665"))
        ).scalars().all()
        assert any(ls.source == "FMCSA Census" for ls in sources)


async def test_two_incoming_rows_sharing_a_dot_merge_in_page(sm):
    """A single page can legitimately carry the same DOT twice (FMCSA emits a
    row twice during a reissue window). Both must land on one lead row — the
    DB-lookup alone wouldn't see the still-uncommitted sibling, so the
    in-page dedup belt is what keeps the page's txn alive.
    """
    started = datetime(2026, 10, 9, 10, 7, tzinfo=UTC)

    a = DiscoveredLead(
        id="DOT-9999001", mc=None, dot="9999001", domain=None,
        name="GHOST BROKER", kind="Broker", state="LA",
        city="BATON ROUGE", address="1 X ST", phone=None, primary_email=None,
        raw={"dot_number": "9999001", "add_date": "20260101"},
    )
    b = DiscoveredLead(
        id="MC-9999002", mc="9999002", dot="9999001", domain=None,
        name="GHOST BROKER", kind="Broker", state="LA",
        city="BATON ROUGE", address="1 X ST", phone=None,
        primary_email="ghost@example.com",
        raw={"dot_number": "9999001", "add_date": "20260101"},
    )

    new_ids, _ = await _upsert_fmcsa_page(sm, [a, b], run_id="run_dup", started=started)
    # Both rows report onto the same id — the DB side settles into one row,
    # which is what matters; new_ids may double-count the shared row.
    assert set(new_ids) == {"DOT-9999001"}, "second row with same DOT must merge, not create a sibling"

    async with sm() as s:
        rows = (await s.execute(select(Lead).where(Lead.dot == "9999001"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == "DOT-9999001"
        assert rows[0].mc == "9999002"  # backfilled from the second row
        assert rows[0].primary_email == "ghost@example.com"
