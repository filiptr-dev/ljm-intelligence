"""Shipper Finder API — filter combinations, cursor stability, promote ladder + idempotency.

DB choice: in-memory SQLite via aiosqlite, same pattern as test_call_list_api.py.
No network (Overpass/FMCSA/Gemini are never called from the read/promote path).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import CallOutcome, CapacityPost, Lead, ShipperCandidate

# ---------------------------------------------------------------------------
# Fixture: fresh in-memory DB + FastAPI app wired to it.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------


async def _seed_candidate(sm, **kw) -> ShipperCandidate:
    defaults = {
        "id": kw.get("id") or f"cand-{kw.get('name', 'x').replace(' ', '-').lower()}",
        "sources": ["FMCSA"],
        "name": "Acme Distribution",
        "state": "NJ",
        "city": "Newark",
        "raw": {},
        "evidence": {},
        "osm_tags": None,
    }
    defaults.update(kw)
    async with sm() as s:
        row = ShipperCandidate(**defaults)
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def _seed_lead(sm, **kw) -> Lead:
    defaults = {
        "id": "MC-EXISTING",
        "name": "Acme Distribution",
        "kind": "Shipper",
        "state": "NJ",
        "city": "Newark",
        "mc": "111111",
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


async def _seed_capacity_post(sm, **kw) -> CapacityPost:
    defaults = {
        "id": "P-1",
        "kind": "truck",
        "equipment": "dry_van",
        "origin_state": "NJ",
        "destinations": ["PA"],
        "status": "open",
    }
    defaults.update(kw)
    async with sm() as s:
        row = CapacityPost(**defaults)
        s.add(row)
        await s.commit()
    return row


# ---------------------------------------------------------------------------
# GET /tools/shipper-finder — filters & shape
# ---------------------------------------------------------------------------


async def test_list_returns_ranked_rows_with_shape(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_candidate(sm, id="c-hot", name="Hot DC", fmcsa_mc="123456", phone="5551234567", state="NJ")
    await _seed_candidate(sm, id="c-cold", name="Cold Place", state="NJ")

    r = await client.get("/tools/shipper-finder")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [row["id"] for row in body["items"]]
    assert "c-hot" in ids and "c-cold" in ids
    assert ids.index("c-hot") < ids.index("c-cold")  # authority beats bare row
    hot = next(row for row in body["items"] if row["id"] == "c-hot")
    assert hot["score"] > 0
    assert isinstance(hot["reasons"], list)
    assert hot["sources"] == ["FMCSA"]
    assert body["next_cursor"] is None


async def test_list_drops_out_of_region_state(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_candidate(sm, id="c-nj", state="NJ")
    # TX is not in the 32-state footprint.
    await _seed_candidate(sm, id="c-tx", state="TX", name="Texas Yard")

    r = await client.get("/tools/shipper-finder")
    ids = [row["id"] for row in r.json()["items"]]
    assert "c-nj" in ids
    assert "c-tx" not in ids


@pytest.mark.parametrize(
    "query, expected_ids",
    [
        ("state=NJ", {"c-nj", "c-osm", "c-both", "c-promoted"}),
        ("state=PA", {"c-pa"}),
        ("source=FMCSA", {"c-nj", "c-pa", "c-both", "c-promoted"}),
        ("source=OSM", {"c-osm", "c-both"}),
        ("source=Both", {"c-both"}),
        ("promoted=true", {"c-promoted"}),
        ("promoted=false", {"c-nj", "c-pa", "c-osm", "c-both"}),
        ("q=Acme", {"c-nj", "c-pa"}),
        ("min_score=40", {"c-nj", "c-pa", "c-both", "c-promoted"}),
    ],
)
async def test_list_filter_combinations(client: AsyncClient, query: str, expected_ids: set[str]):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_candidate(sm, id="c-nj", name="Acme NJ", state="NJ", fmcsa_mc="1")
    await _seed_candidate(sm, id="c-pa", name="Acme PA", state="PA", fmcsa_dot="2")
    await _seed_candidate(
        sm,
        id="c-osm",
        name="Warehouse OSM",
        state="NJ",
        sources=["OSM"],
        fmcsa_mc=None,
        osm_ref="way/1",
        osm_tags={"building": "warehouse", "name": "Warehouse OSM"},
    )
    await _seed_candidate(
        sm,
        id="c-both",
        name="Both Sources",
        state="NJ",
        sources=["FMCSA", "OSM"],
        fmcsa_mc="3",
        osm_ref="way/2",
        osm_tags={"industrial": "distribution_centre", "name": "Both Sources"},
    )
    await _seed_lead(sm, id="L-1", mc="9999")
    await _seed_candidate(
        sm,
        id="c-promoted",
        name="Promoted Co",
        state="NJ",
        fmcsa_mc="9999",
        promoted_lead_id="L-1",
    )

    r = await client.get(f"/tools/shipper-finder?{query}")
    assert r.status_code == 200, r.text
    got = {row["id"] for row in r.json()["items"]}
    assert got == expected_ids, f"query={query}: {got} != {expected_ids}"


async def test_list_cursor_pagination_is_stable_across_pages(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    # Seed 60 candidates with varying scores so the ranked order matters.
    for i in range(60):
        await _seed_candidate(
            sm,
            id=f"c-{i:03d}",
            name=f"Shipper {i:03d}",
            state="NJ",
            fmcsa_mc=str(1000 + i) if i % 2 == 0 else None,  # half get authority
            phone="5551234567" if i % 3 == 0 else None,
        )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        url = "/tools/shipper-finder?limit=25"
        if cursor:
            url += f"&cursor={cursor}"
        r = await client.get(url)
        assert r.status_code == 200, r.text
        body = r.json()
        page_ids = [row["id"] for row in body["items"]]
        # No dupes across pages.
        assert not (set(page_ids) & set(seen)), "duplicate ids across pages"
        seen.extend(page_ids)
        cursor = body["next_cursor"]
        if cursor is None:
            break

    # All 60 seeded should appear across the pages (all in-region).
    assert len(seen) == 60
    assert cursor is None  # exhausted


async def test_list_cursor_invalid_returns_400(client: AsyncClient):
    r = await client.get("/tools/shipper-finder?cursor=!!!not-base64!!!")
    assert r.status_code == 400


async def test_list_limit_below_minimum_is_422(client: AsyncClient):
    r = await client.get("/tools/shipper-finder?limit=10")
    assert r.status_code == 422


async def test_list_limit_above_max_is_422(client: AsyncClient):
    r = await client.get("/tools/shipper-finder?limit=500")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /tools/shipper-finder/promote — happy + dedupe ladder + idempotent
# ---------------------------------------------------------------------------


async def test_promote_happy_creates_lead_and_links_candidate(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_candidate(
        sm,
        id="c-1",
        name="New Warehouse",
        state="NJ",
        sources=["OSM"],
        fmcsa_mc=None,
        osm_ref="way/42",
        osm_tags={"building": "warehouse", "name": "New Warehouse"},
    )
    r = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    lead_id = body["lead_id"]

    async with sm() as s:
        lead = (await s.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
        assert lead.kind == "Shipper"
        assert lead.state == "NJ"
        cand = (await s.execute(select(ShipperCandidate).where(ShipperCandidate.id == "c-1"))).scalar_one()
        assert cand.promoted_lead_id == lead_id


async def test_promote_is_idempotent(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_candidate(sm, id="c-1", name="Acme", state="NJ", fmcsa_mc="12345")

    r1 = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-1"})
    r2 = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-1"})
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["created"] is True
    assert r2.json()["created"] is False
    assert r1.json()["lead_id"] == r2.json()["lead_id"]

    # Only ONE lead row.
    async with sm() as s:
        leads = (await s.execute(select(Lead))).scalars().all()
        assert len(leads) == 1


async def test_promote_dedupes_on_mc(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-55555", mc="55555", name="Existing Co", state="NJ")
    await _seed_candidate(sm, id="c-1", name="Same Company", state="NJ", fmcsa_mc="55555")
    r = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-1"})
    assert r.status_code == 200
    assert r.json() == {"lead_id": "MC-55555", "created": False}

    async with sm() as s:
        leads = (await s.execute(select(Lead))).scalars().all()
        assert len(leads) == 1


async def test_promote_dedupes_on_dot(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="DOT-9", mc=None, dot="9", name="Existing", state="NJ")
    await _seed_candidate(sm, id="c-1", name="Different Name", state="NJ", fmcsa_dot="9", fmcsa_mc=None)
    r = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-1"})
    assert r.status_code == 200
    assert r.json() == {"lead_id": "DOT-9", "created": False}


async def test_promote_dedupes_on_domain(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="DOMAIN-acme.com", mc=None, dot=None, domain="acme.com", name="Acme", state="NJ")
    await _seed_candidate(sm, id="c-1", name="Acme Corp", state="NJ", domain="acme.com", fmcsa_mc=None)
    r = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-1"})
    assert r.status_code == 200
    assert r.json() == {"lead_id": "DOMAIN-acme.com", "created": False}


async def test_promote_dedupes_on_case_insensitive_name_and_state(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(
        sm,
        id="L-NAMESTATE",
        mc=None,
        dot=None,
        domain=None,
        name="Acme Distribution",
        state="NJ",
    )
    await _seed_candidate(
        sm,
        id="c-1",
        name="ACME DISTRIBUTION",
        state="NJ",
        fmcsa_mc=None,
        sources=["OSM"],
        osm_ref="way/7",
    )
    r = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-1"})
    assert r.status_code == 200
    assert r.json() == {"lead_id": "L-NAMESTATE", "created": False}


async def test_promote_unknown_candidate_returns_404(client: AsyncClient):
    r = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "does-not-exist"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /tools/shipper-finder/{id}
# ---------------------------------------------------------------------------


async def test_detail_returns_full_row_with_evidence(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_candidate(
        sm,
        id="c-1",
        name="Acme DC",
        state="NJ",
        fmcsa_mc="777",
        osm_ref="way/9",
        sources=["FMCSA", "OSM"],
        evidence={"fmcsa": {"legal_name": "Acme"}, "osm": {"name": "Acme DC"}},
        osm_tags={"industrial": "distribution_centre", "name": "Acme DC"},
        match_reason="name+city",
    )
    r = await client.get("/tools/shipper-finder/c-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row"]["id"] == "c-1"
    assert body["row"]["sources"] == ["FMCSA", "OSM"]
    assert body["fmcsa_mc"] == "777"
    assert body["osm_ref"] == "way/9"
    assert body["evidence"]["fmcsa"]["legal_name"] == "Acme"
    assert body["promoted_lead"] is None


async def test_detail_includes_promoted_lead_when_present(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-100", mc="100", name="Acme", state="NJ")
    await _seed_candidate(sm, id="c-1", name="Acme", state="NJ", fmcsa_mc="100", promoted_lead_id="MC-100")
    r = await client.get("/tools/shipper-finder/c-1")
    assert r.status_code == 200
    body = r.json()
    assert body["promoted_lead"] is not None
    assert body["promoted_lead"]["id"] == "MC-100"
    assert body["promoted_lead"]["kind"] == "Shipper"


async def test_detail_unknown_candidate_returns_404(client: AsyncClient):
    r = await client.get("/tools/shipper-finder/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Capacity-post reasoning is exposed through the API (integration sanity check)
# ---------------------------------------------------------------------------


async def test_capacity_match_lifts_row_and_emits_chip(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_capacity_post(sm)
    await _seed_candidate(sm, id="c-1", name="NJ Shipper", state="NJ", fmcsa_mc="1")
    r = await client.get("/tools/shipper-finder")
    row = next(x for x in r.json()["items"] if x["id"] == "c-1")
    assert any("Fits your NJ" in reason for reason in row["reasons"])


# ---------------------------------------------------------------------------
# Not-interested drops the promoted row (integration with call_outcomes)
# ---------------------------------------------------------------------------


async def test_not_interested_promoted_lead_is_dropped(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-1", mc="1", name="Foo", state="NJ")
    await _seed_candidate(sm, id="c-1", name="Foo", state="NJ", fmcsa_mc="1", promoted_lead_id="L-1")
    async with sm() as s:
        s.add(CallOutcome(lead_id="L-1", outcome="not_interested", logged_at=datetime.now(UTC)))
        await s.commit()

    r = await client.get("/tools/shipper-finder")
    ids = [row["id"] for row in r.json()["items"]]
    assert "c-1" not in ids


# ---------------------------------------------------------------------------
# Promote then re-list — end-to-end: promoted row shows promoted_lead_id
# ---------------------------------------------------------------------------


async def test_promote_then_relist_shows_promoted_lead_id(client: AsyncClient):
    """After promote the GET list must return promoted_lead_id on the same row.

    Guards the e2e contract: the list query re-reads from DB, so the
    candidate's promoted_lead_id (set inside the promote transaction) must
    be visible on the next GET even without cache invalidation.
    """
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_candidate(
        sm, id="c-fresh", name="Fresh Warehouse", state="NJ", sources=["OSM"], fmcsa_mc=None, osm_ref="way/99"
    )

    r_promote = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-fresh"})
    assert r_promote.status_code == 200
    lead_id = r_promote.json()["lead_id"]
    assert r_promote.json()["created"] is True

    r_list = await client.get("/tools/shipper-finder")
    assert r_list.status_code == 200
    rows = {row["id"]: row for row in r_list.json()["items"]}
    assert "c-fresh" in rows
    assert rows["c-fresh"]["promoted_lead_id"] == lead_id


# ---------------------------------------------------------------------------
# Dedupe ladder precedence — MC rung beats domain rung when both match
# ---------------------------------------------------------------------------


async def test_promote_mc_rung_beats_domain_rung(client: AsyncClient):
    """When MC matches lead A and domain matches lead B, MC wins (first rung).

    This pins the ladder order so a future refactor can't silently change it
    from first-match-wins to something else.
    """
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    # Lead A matches on MC; Lead B matches on domain.
    await _seed_lead(sm, id="L-mc", mc="77777", dot=None, domain=None, name="MC Co", state="NJ")
    await _seed_lead(sm, id="L-domain", mc=None, dot=None, domain="mc-co.com", name="Domain Co", state="NJ")
    # Candidate carries BOTH mc=77777 and domain=mc-co.com — two rungs would fire.
    await _seed_candidate(
        sm,
        id="c-conflict",
        name="Conflict Co",
        state="NJ",
        fmcsa_mc="77777",
        domain="mc-co.com",
    )
    r = await client.post("/tools/shipper-finder/promote", json={"candidate_id": "c-conflict"})
    assert r.status_code == 200
    body = r.json()
    # MC rung must win — the existing lead from the MC match is returned.
    assert body["lead_id"] == "L-mc"
    assert body["created"] is False
    # Lead B (domain match) must NOT have been linked.
    async with sm() as s:
        from sqlalchemy import select as _sel

        from app.models import ShipperCandidate as _SC

        cand = (await s.execute(_sel(_SC).where(_SC.id == "c-conflict"))).scalar_one()
        assert cand.promoted_lead_id == "L-mc"
