"""FMCSA projection + OSM merge-on-ingest (Slice 2b).

In-memory sqlite via aiosqlite (same pattern as test_call_list_api.py — never
writes into the shared Neon dev DB). Covers:

  * FMCSA projection creates rows, only for `kind='Shipper'`.
  * FMCSA projection is idempotent — a second projection of the same lead
    updates `last_seen_at`, doesn't duplicate.
  * FMCSA + matching OSM (name + city, same state) → 1 merged row with both
    sources.
  * FMCSA + non-matching OSM (name matches, no corroborator) → 2 rows.
  * Full `_run_shipper_stage` continues when Overpass raises for one state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import ShipperCandidate
from app.pipeline.shipper_ingest import (
    ingest_osm_incomings,
    project_fmcsa_shippers_to_candidates,
)
from app.pipeline.shipper_merge import IncomingCandidate

NOW = datetime.now(UTC)


@dataclass
class _FakeLead:
    """Duck-typed DiscoveredLead — the projector reads a handful of attrs."""

    id: str
    mc: str | None
    dot: str | None
    name: str
    kind: str
    state: str
    city: str | None = None
    address: str | None = None
    phone: str | None = None
    primary_email: str | None = None
    domain: str | None = None
    raw: dict | None = None


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _all(sm) -> list[ShipperCandidate]:
    async with sm() as s:
        res = await s.execute(select(ShipperCandidate))
        return list(res.scalars().all())


async def test_fmcsa_projection_creates_shipper_only(sm):
    leads = [
        _FakeLead(id="MC-1", mc="1", dot="10", name="Acme Distribution LLC", kind="Shipper", state="NJ", city="Newark"),
        _FakeLead(id="MC-2", mc="2", dot="20", name="Acme Broker LLC", kind="Broker", state="NJ", city="Newark"),
    ]
    async with sm() as s:
        counts = await project_fmcsa_shippers_to_candidates(s, leads, started=NOW)
        await s.commit()
    assert counts == {"projected_new": 1, "projected_updated": 0}
    rows = await _all(sm)
    assert len(rows) == 1
    r = rows[0]
    assert r.state == "NJ"
    assert list(r.sources) == ["FMCSA"]
    assert r.fmcsa_mc == "1"
    assert r.fmcsa_dot == "10"
    assert r.mc == "1" and r.dot == "10"
    assert r.promoted_lead_id == "MC-1"


async def test_fmcsa_projection_idempotent(sm):
    lead = _FakeLead(id="MC-1", mc="1", dot="10", name="Acme LLC", kind="Shipper", state="NJ", city="Newark")
    async with sm() as s:
        await project_fmcsa_shippers_to_candidates(s, [lead], started=NOW)
        await s.commit()
    later = NOW.replace(microsecond=NOW.microsecond // 2)
    async with sm() as s:
        counts = await project_fmcsa_shippers_to_candidates(s, [lead], started=later)
        await s.commit()
    assert counts == {"projected_new": 0, "projected_updated": 1}
    rows = await _all(sm)
    assert len(rows) == 1  # NO duplicate


async def test_fmcsa_plus_matching_osm_merge_into_one_row(sm):
    fmcsa = _FakeLead(
        id="MC-9",
        mc="9",
        dot="99",
        name="Acme Distribution LLC",
        kind="Shipper",
        state="NJ",
        city="Newark",
    )
    async with sm() as s:
        await project_fmcsa_shippers_to_candidates(s, [fmcsa], started=NOW)
        await s.commit()

    osm = IncomingCandidate(
        source="OSM",
        name="Acme Distribution",  # normalises equal to FMCSA name
        state="NJ",
        city="Newark",  # name+city corroborator
        osm_ref="way/1234",
        extra={"tags": {"building": "warehouse", "name": "Acme Distribution"}, "lat": 40.7, "lng": -74.2},
    )
    async with sm() as s:
        counts = await ingest_osm_incomings(s, [osm], started=NOW)
        await s.commit()
    assert counts == {"osm_new": 0, "osm_merged": 1}

    rows = await _all(sm)
    assert len(rows) == 1
    r = rows[0]
    assert set(r.sources) == {"FMCSA", "OSM"}
    assert r.osm_ref == "way/1234"
    assert r.fmcsa_mc == "9"
    assert r.lat == 40.7 and r.lng == -74.2
    assert r.match_reason == "name+city"
    # Evidence carries both source keys.
    assert "fmcsa" in (r.evidence or {})
    assert "osm" in (r.evidence or {})
    assert (r.evidence or {}).get("match", {}).get("rule") == "name+city"


async def test_non_matching_osm_creates_second_row(sm):
    fmcsa = _FakeLead(
        id="MC-9",
        mc="9",
        dot="99",
        name="Acme Distribution LLC",
        kind="Shipper",
        state="NJ",
        city="Newark",
    )
    async with sm() as s:
        await project_fmcsa_shippers_to_candidates(s, [fmcsa], started=NOW)
        await s.commit()

    # Different city, no phone/domain — ambiguous, deliberately new row.
    osm = IncomingCandidate(
        source="OSM",
        name="Acme Distribution",
        state="NJ",
        city="Kearny",
        osm_ref="way/5555",
        extra={"tags": {"building": "warehouse"}, "lat": 40.75, "lng": -74.15},
    )
    async with sm() as s:
        counts = await ingest_osm_incomings(s, [osm], started=NOW)
        await s.commit()
    assert counts == {"osm_new": 1, "osm_merged": 0}

    rows = await _all(sm)
    assert len(rows) == 2


async def test_osm_repeat_hits_same_osm_ref(sm):
    osm = IncomingCandidate(
        source="OSM",
        name="Ford Warehouse",
        state="MI",
        city="Detroit",
        osm_ref="way/7",
        extra={"tags": {"building": "warehouse"}, "lat": 42.3, "lng": -83.0},
    )
    async with sm() as s:
        await ingest_osm_incomings(s, [osm], started=NOW)
        await s.commit()
    async with sm() as s:
        counts = await ingest_osm_incomings(s, [osm], started=NOW)
        await s.commit()
    assert counts == {"osm_new": 0, "osm_merged": 1}
    rows = await _all(sm)
    assert len(rows) == 1


async def test_pipeline_run_shipper_stage_continues_on_overpass_failure(sm, monkeypatch):
    """Full `_run_shipper_stage` — Overpass raises for one state; FMCSA still lands, others continue."""
    from app.pipeline import run as run_mod

    # Two shippers so the FMCSA projection has work.
    fmcsa_leads = [
        _FakeLead(id="MC-1", mc="1", dot="10", name="Acme LLC", kind="Shipper", state="NJ", city="Newark"),
        _FakeLead(id="MC-2", mc="2", dot="20", name="Beta LLC", kind="Broker", state="NJ"),
    ]

    calls: list[str] = []

    async def fake_fetch(state, **kwargs):
        calls.append(state)
        if state == "NJ":
            raise RuntimeError("overpass exploded")
        return []

    monkeypatch.setattr(run_mod, "fetch_overpass_elements", fake_fetch)

    settings = _FakeSettings(
        osm_overpass_enabled=True,
        osm_overpass_states=["NJ", "PA"],
        osm_overpass_max_states=0,
    )
    totals = await run_mod._run_shipper_stage(sm, settings, fmcsa_leads=fmcsa_leads, started=NOW)
    assert calls == ["NJ", "PA"]
    assert totals["shippers_projected"] == 1
    assert totals["osm_states_failed"] == 1  # NJ raised
    rows = await _all(sm)
    assert len(rows) == 1  # only the FMCSA shipper landed


async def test_shipper_stage_respects_disabled_flag(sm, monkeypatch):
    from app.pipeline import run as run_mod

    async def fake_fetch(state, **kwargs):
        raise AssertionError("should not be called when disabled")

    monkeypatch.setattr(run_mod, "fetch_overpass_elements", fake_fetch)
    settings = _FakeSettings(osm_overpass_enabled=False, osm_overpass_states=[], osm_overpass_max_states=0)
    fmcsa_leads = [_FakeLead(id="MC-1", mc="1", dot="10", name="Acme LLC", kind="Shipper", state="NJ")]
    totals = await run_mod._run_shipper_stage(sm, settings, fmcsa_leads=fmcsa_leads, started=NOW)
    assert totals["shippers_projected"] == 1
    assert totals["osm_new"] == 0


@dataclass
class _FakeSettings:
    osm_overpass_enabled: bool
    osm_overpass_states: list
    osm_overpass_max_states: int
