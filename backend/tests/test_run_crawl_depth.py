"""Slice-2/3/4 tests for the FMCSA crawl-depth plan (2026-09-30).

All offline. Uses the same in-memory sqlite pattern as ``test_shipper_ingest.py``
and monkeypatches ``app.pipeline.run.fetch_fmcsa`` with a fake async generator
so the paginator's *pipeline* behaviour can be exercised without hitting SODA.

Covers:
  * AC2 — caught-up stop after N pages when the frontier is reached; the fake
    generator is drained no further than N.
  * AC3 — per-run page cap (``fmcsa_per_run_page_cap``) — stops with reason ``cap``.
  * AC4 — a repeat run with no new rows and the frontier already at page 1's oldest
    ``add_date`` stops after page 1 with ``caught_up`` and ``fmcsa_new=0``.
  * AC5 — Gemini stage: no API key → ``gemini_status="no_api_key"``; discovery
    exception → ``gemini_status="discovery_failed"`` + ``gemini_error`` populated,
    and FMCSA leads still persist.
  * OSM status — reported as ``ok`` / ``partial`` / ``all_failed`` / ``skipped``,
    with the first failure captured in ``osm_error``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import CrawlRun, Lead
from app.prospecting.pipeline import gemini_stage as gemini_stage_mod
from app.prospecting.pipeline import run as run_mod
from app.prospecting.pipeline.run import run_crawl
from app.integrations.adapters.enrichment.fmcsa import DiscoveredLead

# ---------- fixtures / helpers ------------------------------------------------


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@dataclass
class _FakeSettings:
    """Duck-typed Settings — only the fields ``run_crawl`` reads. Numbers +
    ``osm_overpass_enabled=False`` keep the pipeline focused on FMCSA/Gemini."""

    fmcsa_page_size: int = 500
    fmcsa_per_run_page_cap: int = 6
    fmcsa_backfill_page_cap: int = 40
    fmcsa_time_budget_s: float = 120.0
    fmcsa_app_token: Any = None  # SecretStr | None — None here
    osm_overpass_enabled: bool = False
    osm_overpass_states: list = None
    osm_overpass_max_states: int = 0
    gemini_api_key: Any = None
    gemini_model: str = "gemini-3.5-flash-lite"


def _lead(dot: str, add_date: str, state: str = "NJ") -> DiscoveredLead:
    return DiscoveredLead(
        id=f"MC-{dot}",
        mc=dot,
        dot=dot,
        domain=None,
        name=f"Acme {dot}",
        kind="Broker",
        state=state,
        city="Newark",
        address=None,
        phone=None,
        primary_email=None,
        raw={"dot_number": dot, "add_date": add_date, "legal_name": f"Acme {dot}"},
    )


def _make_fake_pages(pages: list[list[DiscoveredLead]]):
    """Build a monkeypatch-friendly async generator that yields the given pages
    up to the caller's ``max_pages`` — mirrors ``fetch_fmcsa``'s cap semantics."""
    call_count = {"n": 0}

    async def fake_fetch_fmcsa(*, page_size, max_pages, app_token=None):
        for i, page in enumerate(pages):
            if i >= max_pages:
                return
            call_count["n"] += 1
            yield page

    fake_fetch_fmcsa.call_count = call_count  # type: ignore[attr-defined]
    return fake_fetch_fmcsa


def _install_fake_fmcsa(monkeypatch, pages: list[list[DiscoveredLead]]):
    faker = _make_fake_pages(pages)
    monkeypatch.setattr(run_mod, "fetch_fmcsa", faker)
    return faker


# ---------- AC2 — caught-up stop ---------------------------------------------


async def test_caught_up_stops_on_frontier(sm, monkeypatch):
    """AC2 — three pages, then page-3's oldest row is at/below frontier and
    yields 0 new → paginator stops with ``caught_up`` and never asks for a 4th."""

    # Pre-seed one already-known lead so the frontier read returns 20260901.
    from datetime import UTC, datetime

    async with sm() as s:
        s.add(
            Lead(
                id="MC-OLD",
                mc="OLD",
                dot="OLD",
                name="Seed",
                kind="Broker",
                state="NJ",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                first_seen_run_id="run_seed",
                last_seen_run_id="run_seed",
                raw={"fmcsa": {"add_date": "20260901"}},
                evidence={},
                recommendations=[],
            )
        )
        await s.commit()

    # Pages 1 & 2 have brand-new rows (add_date > frontier). Page 3 replays the
    # already-known MC-OLD → 0 new AND min add_date == frontier → caught_up.
    p1 = [_lead("1000", "20260930")]
    p2 = [_lead("900", "20260929")]
    p3 = [_lead("OLD", "20260901")]  # same id as seed → 0 new
    fake = _install_fake_fmcsa(monkeypatch, [p1, p2, p3, [_lead("BAD", "19990101")]])

    summary = await run_crawl(sm, _FakeSettings(), trigger="on_demand")
    assert fake.call_count["n"] == 3, "must not fetch page 4"
    assert summary.counts["fmcsa_stopped_reason"] == "caught_up"
    assert summary.counts["fmcsa_pages"] == 3
    assert summary.counts["fmcsa_new"] == 2


# ---------- AC3 — per-run cap -------------------------------------------------


async def test_per_run_page_cap_stops_loop(sm, monkeypatch):
    """AC3 — cap=2, generator has 4 pages available; only 2 are consumed."""

    # Seed a frontier so we're on the *steady-state* cap, not backfill.
    from datetime import UTC, datetime

    async with sm() as s:
        s.add(
            Lead(
                id="MC-SEED",
                mc="SEED",
                dot="SEED",
                name="Seed",
                kind="Broker",
                state="NJ",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                first_seen_run_id="run_seed",
                last_seen_run_id="run_seed",
                raw={"fmcsa": {"add_date": "20260101"}},
                evidence={},
                recommendations=[],
            )
        )
        await s.commit()

    pages = [
        [_lead("1000", "20260930")],
        [_lead("900", "20260929")],
        [_lead("800", "20260928")],
        [_lead("700", "20260927")],
    ]
    fake = _install_fake_fmcsa(monkeypatch, pages)
    settings = _FakeSettings(fmcsa_per_run_page_cap=2)

    summary = await run_crawl(sm, settings, trigger="on_demand")

    assert fake.call_count["n"] == 2
    assert summary.counts["fmcsa_pages"] == 2
    assert summary.counts["fmcsa_stopped_reason"] == "cap"


# ---------- AC4 — repeat run is a one-page no-op ------------------------------


async def test_repeat_run_stops_on_page_one_caught_up(sm, monkeypatch):
    """AC4 — everything on page 1 is already known → 0 new + page min <= frontier
    → stops after page 1 with ``caught_up``, ``fmcsa_new=0``."""

    from datetime import UTC, datetime

    # Seed the same lead we'll "rediscover" on page 1.
    async with sm() as s:
        s.add(
            Lead(
                id="MC-1000",
                mc="1000",
                dot="1000",
                name="Acme 1000",
                kind="Broker",
                state="NJ",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                first_seen_run_id="run_seed",
                last_seen_run_id="run_seed",
                raw={"fmcsa": {"add_date": "20260930"}},
                evidence={},
                recommendations=[],
            )
        )
        await s.commit()

    fake = _install_fake_fmcsa(
        monkeypatch,
        [[_lead("1000", "20260930")], [_lead("999", "20260929")]],
    )

    summary = await run_crawl(sm, _FakeSettings(), trigger="cron")
    assert fake.call_count["n"] == 1
    assert summary.counts["fmcsa_new"] == 0
    assert summary.counts["fmcsa_stopped_reason"] == "caught_up"


# ---------- AC5 — Gemini status visibility -----------------------------------


async def test_gemini_status_no_api_key(sm, monkeypatch):
    """AC5 — no key set → ``gemini_status='no_api_key'``, FMCSA still ships."""
    _install_fake_fmcsa(monkeypatch, [[_lead("1000", "20260930")]])

    summary = await run_crawl(sm, _FakeSettings(gemini_api_key=None), trigger="cron")

    assert summary.counts["gemini_status"] == "no_api_key"
    assert summary.counts["fmcsa_new"] == 1
    # FMCSA row actually persisted.
    async with sm() as s:
        rows = list((await s.execute(select(Lead))).scalars().all())
    assert any(r.id == "MC-1000" for r in rows)


async def test_gemini_status_discovery_failed(sm, monkeypatch):
    """AC5 — discovery raises → ``gemini_status='discovery_failed'`` + ``gemini_error``.
    FMCSA leads must still be committed."""
    _install_fake_fmcsa(monkeypatch, [[_lead("1000", "20260930")]])

    class _KeyStub:
        def get_secret_value(self) -> str:
            return "fake"

    class _BoomDiscoverer:
        def __init__(self, *a, **kw):
            pass

        async def discover(self, target_count=8):
            raise RuntimeError("boom during discovery")

    monkeypatch.setattr(gemini_stage_mod, "GeminiDiscoverer", _BoomDiscoverer)

    settings = _FakeSettings(gemini_api_key=_KeyStub())
    summary = await run_crawl(sm, settings, trigger="cron")

    assert summary.counts["gemini_status"] == "discovery_failed"
    assert "boom during discovery" in summary.counts.get("gemini_error", "")
    # FMCSA lead landed regardless.
    assert summary.counts["fmcsa_new"] == 1
    async with sm() as s:
        rows = list((await s.execute(select(Lead))).scalars().all())
    assert any(r.id == "MC-1000" for r in rows)


async def test_gemini_status_quota_exceeded(sm, monkeypatch):
    """AC5 addendum — quota-shaped exception classifies as ``quota_exceeded``."""
    _install_fake_fmcsa(monkeypatch, [[_lead("1000", "20260930")]])

    class _KeyStub:
        def get_secret_value(self) -> str:
            return "fake"

    class _QuotaDiscoverer:
        def __init__(self, *a, **kw):
            pass

        async def discover(self, target_count=8):
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded for project")

    monkeypatch.setattr(gemini_stage_mod, "GeminiDiscoverer", _QuotaDiscoverer)
    summary = await run_crawl(sm, _FakeSettings(gemini_api_key=_KeyStub()), trigger="cron")

    assert summary.counts["gemini_status"] == "quota_exceeded"
    assert "quota" in summary.counts["gemini_error"].lower()


# ---------- Slice 3 — OSM status ---------------------------------------------


async def test_osm_status_skipped_when_disabled(sm, monkeypatch):
    """OSM disabled → ``osm_status='skipped'`` (no ``osm_error`` key)."""
    _install_fake_fmcsa(monkeypatch, [[_lead("1000", "20260930")]])
    summary = await run_crawl(sm, _FakeSettings(osm_overpass_enabled=False), trigger="cron")
    assert summary.counts["osm_status"] == "skipped"
    assert "osm_error" not in summary.counts


async def test_osm_status_ok_and_partial_and_all_failed(sm, monkeypatch):
    """OSM: two states succeed (``ok``); mixed (``partial``); everything raises (``all_failed``).

    Uses a fake ``fetch_overpass_elements`` monkeypatched onto ``run_mod``.
    """
    _install_fake_fmcsa(monkeypatch, [[_lead("1000", "20260930")]])

    # 1) ok — both states return [].
    async def fake_ok(state, **kwargs):
        return []

    monkeypatch.setattr(run_mod, "fetch_overpass_elements", fake_ok)
    settings = _FakeSettings(
        osm_overpass_enabled=True,
        osm_overpass_states=["NJ", "PA"],
        osm_overpass_max_states=0,
    )
    summary = await run_crawl(sm, settings, trigger="cron")
    assert summary.counts["osm_status"] == "ok"
    assert summary.counts["osm_states_failed"] == 0
    assert "osm_error" not in summary.counts

    # 2) partial — NJ raises, PA succeeds. Repeat with a fresh fake FMCSA.
    _install_fake_fmcsa(monkeypatch, [[_lead("2000", "20260929")]])

    async def fake_partial(state, **kwargs):
        if state == "NJ":
            raise RuntimeError("overpass boom on NJ")
        return []

    monkeypatch.setattr(run_mod, "fetch_overpass_elements", fake_partial)
    summary2 = await run_crawl(sm, settings, trigger="cron")
    assert summary2.counts["osm_status"] == "partial"
    assert summary2.counts["osm_states_failed"] == 1
    assert "NJ" in summary2.counts["osm_error"]
    assert "boom" in summary2.counts["osm_error"]

    # 3) all_failed — every state raises.
    _install_fake_fmcsa(monkeypatch, [[_lead("3000", "20260928")]])

    async def fake_all_fail(state, **kwargs):
        raise RuntimeError(f"{state} down")

    monkeypatch.setattr(run_mod, "fetch_overpass_elements", fake_all_fail)
    summary3 = await run_crawl(sm, settings, trigger="cron")
    assert summary3.counts["osm_status"] == "all_failed"
    assert summary3.counts["osm_states_failed"] == 2


# ---------- Slice 4 — CrawlRun row carries the new keys ----------------------


async def test_crawlrun_row_persists_new_counts_keys(sm, monkeypatch):
    """Verify the CrawlRun row on disk carries the new observability keys, not
    just the in-memory summary — this is what /crawl/runs exposes to the UI."""
    _install_fake_fmcsa(monkeypatch, [[_lead("1000", "20260930")]])
    summary = await run_crawl(sm, _FakeSettings(), trigger="cron")

    async with sm() as s:
        row = (await s.execute(select(CrawlRun).where(CrawlRun.id == summary.id))).scalar_one()
    counts = row.counts or {}
    assert "fmcsa_pages" in counts
    assert "fmcsa_rows_fetched" in counts
    assert "fmcsa_stopped_reason" in counts
    assert "gemini_status" in counts
    assert "osm_status" in counts


# ---- crawl limit (task 2026-10-05 drain-overlap-stuck-jobs, AC3) ----------


@pytest.mark.asyncio
async def test_explicit_limit_caps_rows_fetched(sm, monkeypatch):
    """`/crawl/run?limit=150` used to ingest a full backfill (limit was never
    read). An explicit limit now caps the rows fetched and says why it stopped."""
    pages = [[_lead(f"{p}{i:04d}", "2026-09-01") for i in range(500)] for p in range(1, 4)]
    _install_fake_fmcsa(monkeypatch, pages)

    summary = await run_crawl(sm, _FakeSettings(), trigger="on_demand", fmcsa_limit=150)

    assert summary.counts["fmcsa_rows_fetched"] == 150
    assert summary.counts["fmcsa_stopped_reason"] == "limit"
    async with sm() as s:
        assert len((await s.execute(select(Lead))).scalars().all()) == 150


@pytest.mark.asyncio
async def test_no_limit_keeps_paginator_caps(sm, monkeypatch):
    """No limit = unchanged behaviour: page caps bound the run."""
    pages = [[_lead(f"{p}{i:03d}", "2026-09-01") for i in range(50)] for p in range(1, 4)]
    _install_fake_fmcsa(monkeypatch, pages)

    summary = await run_crawl(sm, _FakeSettings(fmcsa_page_size=50), trigger="on_demand")

    assert summary.counts["fmcsa_rows_fetched"] == 150
    assert summary.counts["fmcsa_stopped_reason"] != "limit"
