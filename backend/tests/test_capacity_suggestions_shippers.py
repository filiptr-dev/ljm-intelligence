"""Capacity suggestions endpoint — shipper-match slice.

Covers the plan's AC1–AC6, AC10, AC11. See
`projects/ljm-intelligence/plan/2026-10-05-capacity-shipper-matches.md`.

In-memory SQLite + ASGITransport, matching `test_brokers_api.py`. No network.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.deps import UserPrincipal, current_user
from app.db import Base
from app.main import create_app
from app.models import CapacityPost, Lead, ShipperCandidate
from app.shared.orm import LJM_TENANT_ID

OTHER_TENANT = "01OTHERTENANT0000000000000"


@pytest.fixture
async def client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()
    app.state.sessionmaker = sessionmaker
    app.dependency_overrides[current_user] = lambda: UserPrincipal(
        id="u1", email="owner@test", role="owner"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._sm = sessionmaker  # type: ignore[attr-defined]
        yield c

    await engine.dispose()


# ---------- seeders --------------------------------------------------------


async def _seed_post(sm, **kw) -> CapacityPost:
    defaults = {
        "id": "CP-truck1",
        "kind": "truck",
        "equipment": "Dry Van",
        "origin_city": "Lincoln Park",
        "origin_state": "NJ",
        "destinations": ["PA", "GA"],
        "status": "open",
    }
    defaults.update(kw)
    async with sm() as s:
        row = CapacityPost(**defaults)
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def _seed_shipper(sm, *, tenant_id: str = LJM_TENANT_ID, **kw) -> ShipperCandidate:
    defaults = {
        "id": "01AAAA0000000000000000000A",
        "tenant_id": tenant_id,
        "sources": ["FMCSA"],
        "name": "Acme Mfg",
        "state": "NJ",
        "city": "Newark",
        "primary_email": "ops@acme.test",
        "phone": "5551112222",
    }
    defaults.update(kw)
    async with sm() as s:
        row = ShipperCandidate(**defaults)
        s.add(row)
        await s.commit()
    return row


async def _seed_broker(sm, **kw) -> Lead:
    defaults = {
        "id": "MC-1",
        "name": "Broker One",
        "kind": "Broker",
        "state": "NJ",
        "mc": "1",
        "current_score": 70,
        "primary_email": "b@b.test",
        "phone": "5559990000",
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


# ---------- tests ----------------------------------------------------------


@pytest.mark.asyncio
async def test_truck_post_shippers_populated_with_lane_matches(client):
    sm = client._sm  # type: ignore[attr-defined]
    post = await _seed_post(sm)
    await _seed_shipper(sm, id="01NJ0000000000000000000001", state="NJ", name="NJ Shipper")
    await _seed_shipper(sm, id="01GA0000000000000000000001", state="GA", name="GA Shipper")
    await _seed_shipper(sm, id="01TX0000000000000000000001", state="TX", name="TX Shipper")

    r = await client.get(f"/capacity/posts/{post.id}/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert "shippers" in body
    states = {(s["state"], s["name"]) for s in body["shippers"]}
    assert ("NJ", "NJ Shipper") in states
    assert ("GA", "GA Shipper") in states
    # TX candidate must be absent (AC2).
    assert all(s["state"] != "TX" for s in body["shippers"])

    # Reason strings — AC2.
    nj = next(s for s in body["shippers"] if s["state"] == "NJ")
    ga = next(s for s in body["shippers"] if s["state"] == "GA")
    assert "your truck origin" in nj["reason"]
    assert "one of your truck's destinations" in ga["reason"]


@pytest.mark.asyncio
async def test_load_post_shippers_drop_state(client):
    sm = client._sm  # type: ignore[attr-defined]
    post = await _seed_post(
        sm, id="CP-load1", kind="load", destinations=[], dest_state="GA", dest_city="Atlanta"
    )
    await _seed_shipper(sm, id="01GAX000000000000000000001", state="GA", name="GA Drop Co")

    r = await client.get(f"/capacity/posts/{post.id}/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert len(body["shippers"]) == 1
    assert "your load's drop state" in body["shippers"][0]["reason"]


@pytest.mark.asyncio
async def test_tenant_isolation_shipper_candidates(client):
    """AC6 — shipper from a different tenant must never leak into another's suggestions."""
    sm = client._sm  # type: ignore[attr-defined]
    post = await _seed_post(sm, id="CP-tenant")
    # Other-tenant NJ candidate — must NOT appear.
    await _seed_shipper(
        sm, id="01OTHER000000000000000000A", state="NJ", name="Other Tenant Co", tenant_id=OTHER_TENANT
    )
    # Our tenant has none.
    r = await client.get(f"/capacity/posts/{post.id}/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["shippers"] == []


@pytest.mark.asyncio
async def test_empty_lane_returns_empty_shippers_200(client):
    """AC10 wire — API returns 200 with shippers: [] for a lane with no candidates."""
    sm = client._sm  # type: ignore[attr-defined]
    post = await _seed_post(sm, id="CP-empty", destinations=["WA"])
    r = await client.get(f"/capacity/posts/{post.id}/suggestions")
    assert r.status_code == 200
    assert r.json()["shippers"] == []


@pytest.mark.asyncio
async def test_broker_items_unchanged_additive_field(client):
    """AC1/AC11 — items (broker suggestions) remain populated independently of shippers."""
    sm = client._sm  # type: ignore[attr-defined]
    post = await _seed_post(sm, id="CP-broker1")
    await _seed_broker(sm)
    # No shipper candidates seeded.
    r = await client.get(f"/capacity/posts/{post.id}/suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["shippers"] == []
    assert any(it["lead_id"] == "MC-1" for it in body["items"])


@pytest.mark.asyncio
async def test_deterministic_sort_across_two_calls(client):
    """AC5 — same seeded data, same order across calls."""
    sm = client._sm  # type: ignore[attr-defined]
    post = await _seed_post(sm, id="CP-det")
    await _seed_shipper(sm, id="01A000000000000000000000A1", state="NJ", name="Alpha Co")
    await _seed_shipper(sm, id="01Z000000000000000000000Z1", state="NJ", name="Zeta Co")
    await _seed_shipper(sm, id="01G000000000000000000000G1", state="GA", name="GA Co")

    r1 = (await client.get(f"/capacity/posts/{post.id}/suggestions")).json()["shippers"]
    r2 = (await client.get(f"/capacity/posts/{post.id}/suggestions")).json()["shippers"]
    assert [s["candidate_id"] for s in r1] == [s["candidate_id"] for s in r2]
