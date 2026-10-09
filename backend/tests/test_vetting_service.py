"""Vetting — service composition + pure-rule edges.

In-memory SQLite via the same pattern as test_call_list_api: skip the real
lifespan, hand-wire ``app.state.sessionmaker``, inject a fake FMCSA port.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import Lead
from app.prospecting.models import FmcsaSnapshotCache
from app.vetting.domain import (
    BrokerSnapshot,
    PriorContact,
    RedFlag,
    VerdictBand,
    normalize_key,
    vet,
)

TODAY = datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Pure rule table — one happy, one "revoked" edge.
# ---------------------------------------------------------------------------


def test_vet_safe_when_authority_old_and_phone_present():
    snap = BrokerSnapshot(
        mc="123",
        dot="999",
        legal_name="Acme Carriers",
        dba_name="Acme Carriers",
        authority_status="A",
        add_date=TODAY - timedelta(days=365 * 3),
        oos_date=None,
        phone="5551234567",
        email="ops@acme.example",
    )
    v = vet(snap, PriorContact(booked_count=2), suppressed=False, today=TODAY)
    assert v.band is VerdictBand.safe
    assert v.red_flags == []


def test_vet_avoid_when_authority_revoked():
    snap = BrokerSnapshot(
        mc="1",
        dot="1",
        legal_name="Bad Co",
        dba_name=None,
        authority_status="R",
        add_date=TODAY - timedelta(days=365),
        oos_date=None,
        phone="5550000000",
        email=None,
    )
    v = vet(snap, PriorContact(), suppressed=False, today=TODAY)
    assert v.band is VerdictBand.avoid
    assert RedFlag.authority_revoked in {f.code for f in v.red_flags}


def test_vet_caution_on_new_authority():
    snap = BrokerSnapshot(
        mc="2", dot="2", legal_name="New Co", dba_name=None,
        authority_status="A", add_date=TODAY - timedelta(days=10),
        oos_date=None, phone="5551112222", email=None,
    )
    v = vet(snap, PriorContact(), suppressed=False, today=TODAY)
    assert v.band is VerdictBand.caution
    assert RedFlag.new_authority_lt_180d in {f.code for f in v.red_flags}


def test_normalize_key_strips_prefix_and_rejects_nondigits():
    assert normalize_key("MC-123456") == "123456"
    assert normalize_key("DOT 2000") == "2000"
    assert normalize_key("abc") is None
    assert normalize_key("") is None


# ---------------------------------------------------------------------------
# HTTP route — happy path hits cache, cache-miss edge hits the fake live port.
# ---------------------------------------------------------------------------


class _FakeFmcsa:
    def __init__(self, payload: dict | None):
        self.payload = payload
        self.calls = 0

    async def fetch(self, dot: str) -> dict | None:
        self.calls += 1
        return self.payload


@pytest.fixture
async def client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()
    app.state.sessionmaker = sessionmaker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._test_sessionmaker = sessionmaker  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


async def _seed_lead(sm, **kw) -> Lead:
    defaults = {
        "id": "MC-123456",
        "mc": "123456",
        "dot": "9876543",
        "name": "Cached Carrier",
        "kind": "Broker",
        "state": "NJ",
        "city": "Newark",
        "phone": "5550000000",
        "primary_email": "ops@cached.example",
        "current_score": 50,
        "raw": {},
        "evidence": {},
        "recommendations": [],
    }
    defaults.update(kw)
    async with sm() as s:
        row = Lead(**defaults)
        s.add(row)
        await s.commit()
    return row


async def _seed_snapshot(sm, dot: str, payload: dict, fetched_at: datetime | None = None):
    async with sm() as s:
        row = FmcsaSnapshotCache(dot=dot, payload=payload, fetched_at=fetched_at or datetime.now(UTC))
        s.add(row)
        await s.commit()


def _payload(**overrides) -> dict:
    carrier = {
        "legalName": "Cached Carrier",
        "dbaName": "Cached Carrier",
        "statusCode": "A",
        "addDate": "20200101",
        "telephone": "5550000000",
        "emailAddress": "ops@cached.example",
    }
    carrier.update(overrides)
    return {"content": {"carrier": carrier}}


async def test_get_vetting_hits_cache(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm)
    await _seed_snapshot(sm, "9876543", _payload())

    r = await client.get("/vetting/MC-123456")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "safe"
    assert body["mc"] == "123456"
    assert body["dot"] == "9876543"
    assert body["authority"]["status"] == "A"
    assert body["stale"] is False
    assert body["lead_id"] == "MC-123456"


async def test_get_vetting_422_on_bad_key(client: AsyncClient):
    r = await client.get("/vetting/not-a-number")
    assert r.status_code == 422


async def test_get_vetting_falls_back_to_lead_when_live_fails(
    client: AsyncClient, monkeypatch
):
    """Cache-miss + live-fail + lead on file → 200 stale snapshot (not 404).

    Covers the 2026-10-09 prod audit regression: MC-50975720 hit a 15s hang
    and 404 because the code path raised FmcsaUnreachableError whenever the
    live call failed, ignoring the lead row we already had. The fallback
    keeps the UX whole and reserves 503 for 'nothing on file anywhere'.
    """
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(
        sm, id="MC-55555", mc="55555", dot="7777777",
        name="Fallback Carrier", phone="5559990000",
        primary_email="ops@fallback.example",
    )

    async def _dead_live(dot):
        return None

    from app.vetting import repository as vrepo

    monkeypatch.setattr(vrepo, "live_fmcsa_fetch", _dead_live)
    r = await client.get("/vetting/MC-55555")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale"] is True
    assert body["legal_name"] == "Fallback Carrier"
    assert body["mc"] == "55555"
    assert body["lead_id"] == "MC-55555"


async def test_get_vetting_503_when_truly_unknown(
    client: AsyncClient, monkeypatch
):
    """No lead, no cache, live fails → 503 (upstream unreachable), not 404.

    404 is reserved for 'this MC/DOT genuinely does not exist'; a flaky
    FMCSA snapshot endpoint is a transient dependency failure.
    """
    async def _dead_live(dot):
        return None

    from app.vetting import repository as vrepo

    monkeypatch.setattr(vrepo, "live_fmcsa_fetch", _dead_live)
    r = await client.get("/vetting/DOT-9999999")
    assert r.status_code == 503
    assert r.headers.get("Retry-After") == "30"


def test_vet_caution_when_authority_status_is_unknown():
    """Lead-fallback snapshots carry no authority_status; the rule table
    must flag ``authority_unverified`` and route to caution — never safe.
    Regression guard for the 2026-10-09 audit finding: a fabricated 'A'
    status let an un-vetted lead ride through as a 'safe' verdict.
    """
    snap = BrokerSnapshot(
        mc="55555",
        dot=None,
        legal_name="Lead-Only Carrier",
        dba_name=None,
        authority_status=None,  # explicit unknown — FMCSA never confirmed
        add_date=None,
        oos_date=None,
        phone="5551110000",
        email="ops@leadonly.example",
        source="lead_record",
    )
    v = vet(snap, PriorContact(booked_count=3), suppressed=False, today=TODAY)
    assert v.band is not VerdictBand.safe
    assert v.band is VerdictBand.caution
    assert RedFlag.authority_unverified in {f.code for f in v.red_flags}


async def test_lead_fallback_does_not_produce_safe_verdict(
    client: AsyncClient, monkeypatch
):
    """End-to-end: lead on file + FMCSA dead → stale lead_record snapshot
    with verdict=caution and source='lead_record'. The response must NOT
    claim authority is active just because enrichment once ingested it.
    """
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(
        sm, id="MC-66666", mc="66666", dot="8888888",
        name="Unverified Fallback Co", phone="5557778888",
        primary_email="ops@unverified.example",
    )

    async def _dead_live(dot):
        return None

    from app.vetting import repository as vrepo

    monkeypatch.setattr(vrepo, "live_fmcsa_fetch", _dead_live)
    r = await client.get("/vetting/MC-66666")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] != "safe"
    assert body["verdict"] == "caution"
    assert body["authority"]["source"] == "lead_record"
    assert body["authority"]["status"] is None
    assert any(f["code"] == "authority_unverified" for f in body["red_flags"])


async def test_mc_prefix_is_honoured_even_for_8_digit_numbers(
    client: AsyncClient, monkeypatch
):
    """Explicit 'MC-' must route to the MC column — the length heuristic
    (>=7 digits → DOT) must not override a user-supplied prefix. This was
    the exact failure on MC-50975720 (8 digits) in the 2026-10-09 audit.
    """
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(
        sm, id="MC-50975720", mc="50975720", dot=None,
        name="Eight-Digit MC Co",
    )

    async def _dead_live(dot):
        # Should never be called — no DOT to look up, lead fallback takes over.
        raise AssertionError(f"live called with dot={dot}; should have used lead fallback")

    from app.vetting import repository as vrepo

    monkeypatch.setattr(vrepo, "live_fmcsa_fetch", _dead_live)
    r = await client.get("/vetting/MC-50975720")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mc"] == "50975720"
    assert body["stale"] is True
    assert body["legal_name"] == "Eight-Digit MC Co"
