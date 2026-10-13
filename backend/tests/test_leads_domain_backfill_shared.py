"""leads_domain_backfill: shared-domain skip + idempotency on SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Lead
from app.prospecting.leads_domain_backfill import run_on

pytestmark = pytest.mark.asyncio


async def _sm():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(e, expire_on_commit=False)


async def test_shared_candidate_domain_is_skipped_not_crashed():
    """Two leads sharing one real company domain (franchises like
    unishippers.com) must both be reported as ``skipped_shared`` and
    neither must be written — matches the live-DB IntegrityError that
    triggered this fix (DOT-9024552)."""
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ",
                   primary_email="a@unishippers.com"))
        s.add(Lead(id="B", name="B", kind="Broker", state="NJ",
                   primary_email="b@unishippers.com"))
        # A third lead with a unique domain still gets backfilled.
        s.add(Lead(id="C", name="C", kind="Broker", state="NJ",
                   primary_email="c@acme.com"))
        await s.commit()

    out = await run_on(sm)
    assert out["scanned"] == 3
    assert out["updated"] == 1
    assert out["skipped_shared"] == 2
    assert out["skipped_taken"] == 0

    async with sm() as s:
        rows = {r.id: r.domain for r in (await s.execute(select(Lead))).scalars().all()}
    assert rows["A"] is None
    assert rows["B"] is None
    assert rows["C"] == "acme.com"


async def test_candidate_matches_already_claimed_domain_is_skipped():
    """A lead whose candidate domain is already set on another lead must
    be ``skipped_taken`` (never written) — defends against the UNIQUE
    collision at the SQL layer and is a key enabler of safe re-runs."""
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ",
                   primary_email="x@acme.com", domain="acme.com"))
        s.add(Lead(id="B", name="B", kind="Broker", state="NJ",
                   primary_email="y@acme.com"))
        await s.commit()

    out = await run_on(sm)
    assert out["scanned"] == 1  # A already has a domain
    assert out["updated"] == 0
    assert out["skipped_taken"] == 1

    async with sm() as s:
        rows = {r.id: r.domain for r in (await s.execute(select(Lead))).scalars().all()}
    assert rows["A"] == "acme.com"
    assert rows["B"] is None


async def test_idempotent_rerun():
    """Second run on the same DB updates nothing — nothing is scanned
    (all leads already have a domain or no candidate)."""
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ",
                   primary_email="a@acme.com"))
        await s.commit()
    first = await run_on(sm)
    assert first["updated"] == 1
    second = await run_on(sm)
    assert second["scanned"] == 0 and second["updated"] == 0
