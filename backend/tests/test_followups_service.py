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


async def test_set_stage_persists_override(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _lead(sm, id="MC-DRAG")

    # Starts in NEW (no touches).
    r = await client.get("/followups")
    assert [c["lead_id"] for c in r.json()["new"]] == ["MC-DRAG"]

    # Drag to Replied.
    r = await client.patch("/followups/MC-DRAG/stage", json={"stage": "replied"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "lead_id": "MC-DRAG", "stage": "replied"}

    r2 = await client.get("/followups")
    body = r2.json()
    assert [c["lead_id"] for c in body["replied"]] == ["MC-DRAG"]
    assert body["new"] == []


async def test_set_stage_422_on_bad_value(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _lead(sm, id="MC-BAD")
    r = await client.patch("/followups/MC-BAD/stage", json={"stage": "nope"})
    assert r.status_code == 422


async def test_set_stage_404_on_unknown_lead(client: AsyncClient):
    r = await client.patch("/followups/MC-ghost/stage", json={"stage": "replied"})
    assert r.status_code == 404


async def test_newer_event_wins_over_override(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _lead(sm, id="MC-NEWER", fit_score=80)

    # Override to replied in the past, then a booked call arrives now.
    # The override's timestamp will be "now" (service stamps UTC); we need
    # the booked event to be strictly newer, so push override_at into the
    # past by hand after the PATCH.
    r = await client.patch("/followups/MC-NEWER/stage", json={"stage": "replied"})
    assert r.status_code == 200

    # Back-date the override so the booked event we add next clearly wins.
    from datetime import timedelta
    from sqlalchemy import update
    from app.followups.models import FollowupNote
    async with sm() as s:
        await s.execute(
            update(FollowupNote)
            .where(FollowupNote.lead_id == "MC-NEWER")
            .values(stage_override_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await s.commit()

    await _send(sm, lead_id="MC-NEWER", replied=True)
    await _call(sm, lead_id="MC-NEWER", outcome="booked")

    r2 = await client.get("/followups")
    body = r2.json()
    assert [c["lead_id"] for c in body["booked"]] == ["MC-NEWER"]
    assert body["replied"] == []


async def test_note_and_stage_upserts_do_not_clobber_each_other(client: AsyncClient):
    """Standing rule: note upsert and stage upsert share a row; each must
    only write its own columns.
    """
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _lead(sm, id="MC-SHARED")

    # Note first, then stage, then note again — each write must preserve
    # whatever the other wrote.
    r1 = await client.post(
        "/followups/MC-SHARED/note",
        json={"note": "first", "next_touch": "2026-12-01"},
    )
    assert r1.status_code == 200

    r2 = await client.patch(
        "/followups/MC-SHARED/stage", json={"stage": "contacted"}
    )
    assert r2.status_code == 200

    # Board shows contacted (override) + original note.
    board_body = (await client.get("/followups")).json()
    contacted = board_body["contacted"]
    assert [c["lead_id"] for c in contacted] == ["MC-SHARED"]
    assert contacted[0]["note"] == "first"
    assert contacted[0]["next_touch"] == "2026-12-01"

    # Overwrite note — override must survive.
    r3 = await client.post(
        "/followups/MC-SHARED/note",
        json={"note": "second", "next_touch": None},
    )
    assert r3.status_code == 200
    board_body = (await client.get("/followups")).json()
    contacted = board_body["contacted"]
    assert [c["lead_id"] for c in contacted] == ["MC-SHARED"]
    assert contacted[0]["note"] == "second"
    assert contacted[0]["next_touch"] is None

    # Overwrite stage — note must survive.
    r4 = await client.patch(
        "/followups/MC-SHARED/stage", json={"stage": "booked"}
    )
    assert r4.status_code == 200
    board_body = (await client.get("/followups")).json()
    booked = board_body["booked"]
    assert [c["lead_id"] for c in booked] == ["MC-SHARED"]
    assert booked[0]["note"] == "second"


async def test_stage_upsert_creates_row_when_absent(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _lead(sm, id="MC-FRESH")

    r = await client.patch("/followups/MC-FRESH/stage", json={"stage": "booked"})
    assert r.status_code == 200
    body = (await client.get("/followups")).json()
    assert [c["lead_id"] for c in body["booked"]] == ["MC-FRESH"]
    # Note stays empty (server default) — no accidental "None" stringification.
    assert body["booked"][0]["note"] in (None, "")
