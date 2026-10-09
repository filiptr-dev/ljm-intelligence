"""Follow-ups — board composition + note upsert.

Mutual exclusion is asserted via the standard pipeline: a lead with both
a booked outcome and a reply surfaces in `booked` only; a brand-new lead
with no touches lands in `new`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import CallOutcome, Lead, SentLog


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


async def _lead(sm, **kw):
    defaults = {
        "id": kw.get("id", "MC-1"),
        "mc": kw.get("id", "MC-1").replace("MC-", ""),
        "name": "Broker " + kw.get("id", "MC-1"),
        "kind": "Broker",
        "state": "NJ",
        "city": "Newark",
        "phone": "5551234567",
        "primary_email": kw.get("id", "x") + "@example.com",
        "current_score": 50,
        "fit_score": kw.pop("fit_score", 50),
        "raw": {},
        "evidence": {},
        "recommendations": [],
    }
    defaults.update(kw)
    async with sm() as s:
        row = Lead(**defaults)
        s.add(row)
        await s.commit()


async def _send(sm, *, lead_id, replied: bool = False, sent_at=None):
    async with sm() as s:
        s.add(
            SentLog(
                lead_id=lead_id,
                mode="simulated",
                to_email="x@example.com",
                sent_at=sent_at or datetime.now(UTC) - timedelta(days=3),
                replied_at=(datetime.now(UTC) if replied else None),
            )
        )
        await s.commit()


async def _call(sm, *, lead_id, outcome):
    async with sm() as s:
        s.add(CallOutcome(lead_id=lead_id, outcome=outcome))
        await s.commit()


async def test_board_empty_when_no_data(client: AsyncClient):
    r = await client.get("/followups")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"new": [], "contacted": [], "replied": [], "booked": []}


async def test_board_sorts_leads_into_four_columns(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _lead(sm, id="MC-NEW", fit_score=90)
    await _lead(sm, id="MC-SENT", fit_score=70)
    await _send(sm, lead_id="MC-SENT")
    await _lead(sm, id="MC-REPLIED", fit_score=60)
    await _send(sm, lead_id="MC-REPLIED", replied=True)
    await _lead(sm, id="MC-BOOKED", fit_score=50)
    await _send(sm, lead_id="MC-BOOKED", replied=True)
    await _call(sm, lead_id="MC-BOOKED", outcome="booked")

    r = await client.get("/followups")
    body = r.json()
    assert [c["lead_id"] for c in body["new"]] == ["MC-NEW"]
    assert [c["lead_id"] for c in body["contacted"]] == ["MC-SENT"]
    assert [c["lead_id"] for c in body["replied"]] == ["MC-REPLIED"]
    assert [c["lead_id"] for c in body["booked"]] == ["MC-BOOKED"]
    # Precedence: booked wins over replied even though MC-BOOKED has a reply.
    all_ids = {
        c["lead_id"]
        for col in body.values()
        for c in col
    }
    assert all_ids == {"MC-NEW", "MC-SENT", "MC-REPLIED", "MC-BOOKED"}
    # days_waiting present on the contacted card.
    assert body["contacted"][0]["days_waiting"] is not None
    # Next-action pill shape.
    assert body["new"][0]["next_action"]["kind"] in {"call", "email", "wait"}


async def test_save_note_upserts(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _lead(sm, id="MC-NOTE")

    r = await client.post(
        "/followups/MC-NOTE/note",
        json={"note": "worth another call tuesday", "next_touch": "2026-11-01"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["lead_id"] == "MC-NOTE"

    # GET board reflects the note.
    r2 = await client.get("/followups")
    cards = r2.json()["new"]
    assert cards[0]["note"] == "worth another call tuesday"
    assert cards[0]["next_touch"] == "2026-11-01"

    # Second POST overwrites.
    r3 = await client.post(
        "/followups/MC-NOTE/note",
        json={"note": "left voicemail", "next_touch": None},
    )
    assert r3.status_code == 200
    r4 = await client.get("/followups")
    assert r4.json()["new"][0]["note"] == "left voicemail"
    assert r4.json()["new"][0]["next_touch"] is None


async def test_save_note_404_on_unknown_lead(client: AsyncClient):
    r = await client.post("/followups/MC-ghost/note", json={"note": "x"})
    assert r.status_code == 404
