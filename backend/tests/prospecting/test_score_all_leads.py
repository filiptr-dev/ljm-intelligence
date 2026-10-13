"""Backfill-fit-score sweep: every lead gets a deterministic score and the
AI-written ``current_score`` is never clobbered."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Lead, LeadContact
from app.prospecting.score_all_leads import score_all_leads

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(sm, **extra) -> None:
    async with sm() as s:
        lead = Lead(
            id=extra.pop("id"),
            name=extra.pop("name"),
            kind=extra.pop("kind", "Broker"),
            state=extra.pop("state", "NJ"),
            raw={},
            evidence={},
            recommendations=[],
            **extra,
        )
        s.add(lead)
        await s.commit()


async def test_sweep_backfills_fit_and_current_score(sm):
    await _seed(sm, id="MC-1", name="InRegion Co", state="NJ", current_score=None)
    async with sm() as s:
        s.add(LeadContact(lead_id="MC-1", email="jane@acme.com", title="VP Logistics", is_decision_maker=True))
        await s.commit()

    stats = await score_all_leads(sm, batch_size=10)

    assert stats.scanned == 1
    assert stats.updated == 1
    async with sm() as s:
        lead = await s.get(Lead, "MC-1")
        assert lead.fit_score is not None and lead.fit_score > 0
        assert lead.fit_reasons and "In-region (NJ)" in lead.fit_reasons
        assert lead.fit_computed_at is not None
        # current_score was NULL → backfilled to the fit score
        assert lead.current_score == lead.fit_score


async def test_sweep_preserves_existing_ai_current_score(sm):
    await _seed(sm, id="MC-2", name="Already Scored", state="OH", current_score=73)

    await score_all_leads(sm)

    async with sm() as s:
        lead = await s.get(Lead, "MC-2")
        assert lead.fit_score is not None  # deterministic fit written
        assert lead.current_score == 73  # AI score untouched


async def test_dry_run_writes_nothing(sm):
    await _seed(sm, id="MC-3", name="Dry", state="PA", current_score=None)

    stats = await score_all_leads(sm, dry_run=True)

    assert stats.scanned == 1
    assert stats.buckets  # distribution computed
    async with sm() as s:
        lead = await s.get(Lead, "MC-3")
        assert lead.fit_score is None
        assert lead.current_score is None


async def test_only_missing_skips_already_scored(sm):
    await _seed(sm, id="MC-4", name="Fresh", state="NJ", current_score=None)
    await _seed(sm, id="MC-5", name="Done", state="NJ", current_score=None, fit_score=42)
    async with sm() as s:
        from datetime import UTC, datetime

        done = await s.get(Lead, "MC-5")
        done.fit_computed_at = datetime.now(UTC)
        done.fit_reasons = ["pre-existing"]
        await s.commit()

    stats = await score_all_leads(sm, only_missing=True)

    assert stats.scanned == 1  # only MC-4 looked at
    async with sm() as s:
        assert (await s.get(Lead, "MC-5")).fit_score == 42  # untouched
