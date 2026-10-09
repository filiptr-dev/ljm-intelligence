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


async def test_get_vetting_404_when_no_cache_and_live_fails(
    client: AsyncClient, monkeypatch
):
    """Cache-miss edge: live fetch returns None → 404."""
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-55555", mc="55555", dot="7777777")

    async def _dead_live(dot):
        return None

    from app.vetting import repository as vrepo

    monkeypatch.setattr(vrepo, "live_fmcsa_fetch", _dead_live)
    r = await client.get("/vetting/MC-55555")
    assert r.status_code == 404
