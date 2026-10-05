"""Brokers API — list filters, cursor, detail shape, next-action wiring.

In-memory SQLite + ASGITransport, matching ``test_shipper_finder_api.py``.
No network. Owner-only guard is applied at mount; we assert the unauthenticated
path returns 401 then stamp the dependency override for subsequent calls (same
style as other API tests in this repo).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.deps import UserPrincipal, current_user
from app.db import Base
from app.main import create_app
from app.models import CallOutcome, Lead, LeadContact, SentLog


@pytest.fixture
async def client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()
    app.state.sessionmaker = sessionmaker
    # Bypass the bearer-only guard for tests.
    app.dependency_overrides[current_user] = lambda: UserPrincipal(id="u1", email="owner@test", role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._sm = sessionmaker  # type: ignore[attr-defined]
        yield c

    await engine.dispose()


# ---------- seed helpers ---------------------------------------------------


async def _seed_lead(sm, **kw) -> Lead:
    defaults = {
        "id": "L-1",
        "name": "Acme Brokerage",
        "kind": "Broker",
        "state": "NJ",
        "mc": "100",
        "phone": "5551234567",
        "primary_email": "ops@acme.test",
        "raw": {},
        "evidence": {},
        "recommendations": [],
        "fit_score": 70,
    }
    defaults.update(kw)
    async with sm() as s:
        row = Lead(**defaults)
        s.add(row)
        await s.commit()
    return row


async def _seed_contact(sm, lead_id: str, **kw) -> LeadContact:
    defaults = {
        "lead_id": lead_id,
        "name": "Jane Doe",
        "title": "Ops Manager",
        "email": "jane@acme.test",
        "phone": "5559876543",
        "source": "Gemini",
        "is_decision_maker": True,
        "pipeline_status": "found",
    }
    defaults.update(kw)
    async with sm() as s:
        row = LeadContact(**defaults)
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def _seed_call(sm, lead_id: str, outcome: str, days_ago: int = 1, **kw) -> None:
    row = CallOutcome(
        lead_id=lead_id,
        outcome=outcome,
        logged_at=datetime.now(UTC) - timedelta(days=days_ago),
        **kw,
    )
    async with sm() as s:
        s.add(row)
        await s.commit()


async def _seed_sent(sm, lead_id: str, days_ago: int = 1, replied: bool = False) -> None:
    sent_at = datetime.now(UTC) - timedelta(days=days_ago)
    row = SentLog(
        lead_id=lead_id,
        mode="simulated",
        to_email="ops@acme.test",
        subject="Hi",
        sent_at=sent_at,
        replied_at=sent_at + timedelta(hours=1) if replied else None,
    )
    async with sm() as s:
        s.add(row)
        await s.commit()


# ---------- /brokers list --------------------------------------------------


async def test_list_only_returns_brokers(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-broker", kind="Broker", name="Broker Co", mc="1")
    await _seed_lead(sm, id="L-shipper", kind="Shipper", name="Shipper Co", mc="2")

    r = await client.get("/brokers")
    assert r.status_code == 200, r.text
    ids = [row["id"] for row in r.json()["items"]]
    assert "L-broker" in ids and "L-shipper" not in ids


async def test_list_filters(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-nj", state="NJ", name="NJ Co", mc="1", fit_score=90, phone=None)
    await _seed_lead(sm, id="L-pa", state="PA", name="PA Co", mc="2", fit_score=40, primary_email=None)

    # state filter
    r = await client.get("/brokers?state=NJ")
    assert {x["id"] for x in r.json()["items"]} == {"L-nj"}

    # min_fit
    r = await client.get("/brokers?min_fit=80")
    assert {x["id"] for x in r.json()["items"]} == {"L-nj"}

    # has_phone=false
    r = await client.get("/brokers?has_phone=false")
    assert {x["id"] for x in r.json()["items"]} == {"L-nj"}

    # has_email=false
    r = await client.get("/brokers?has_email=false")
    assert {x["id"] for x in r.json()["items"]} == {"L-pa"}

    # q
    r = await client.get("/brokers?q=PA")
    assert {x["id"] for x in r.json()["items"]} == {"L-pa"}


async def test_list_next_action_filter_and_sort(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    # L-call: fresh phone, no contact -> rule 8 "call"
    await _seed_lead(sm, id="L-call", mc="A", phone="5550000001", primary_email=None, fit_score=50)
    # L-wait: booked 5d ago -> rule 2 "wait"
    await _seed_lead(sm, id="L-wait", mc="B", phone="5550000002", fit_score=90)
    await _seed_call(sm, "L-wait", "booked", days_ago=5)

    r = await client.get("/brokers")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # Sort: call priority (1) before wait priority (4).
    assert [x["id"] for x in items] == ["L-call", "L-wait"]
    kinds = {x["id"]: x["next_action"]["kind"] for x in items}
    assert kinds["L-call"] == "call"
    assert kinds["L-wait"] == "wait"

    # Filter.
    r = await client.get("/brokers?next_action=wait")
    assert {x["id"] for x in r.json()["items"]} == {"L-wait"}


async def test_list_cursor_pagination(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    for i in range(5):
        await _seed_lead(sm, id=f"L-{i}", mc=str(100 + i), name=f"Co {i}")

    r = await client.get("/brokers?limit=2")
    body = r.json()
    first_ids = [x["id"] for x in body["items"]]
    assert len(first_ids) == 2
    assert body["next_cursor"] is not None

    r2 = await client.get(f"/brokers?limit=2&cursor={body['next_cursor']}")
    body2 = r2.json()
    second_ids = [x["id"] for x in body2["items"]]
    assert len(second_ids) == 2
    assert set(first_ids).isdisjoint(set(second_ids))


# ---------- /brokers/{id} detail ------------------------------------------


async def test_detail_full_contact_block(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(
        sm,
        id="L-1",
        primary_email_source="FMCSA Census",
        phone_source="FMCSA Census",
        address_source="Gemini",
        address="123 Main St",
    )
    await _seed_contact(sm, "L-1", name="Jane Doe", email="jane@acme.test", source="Gemini")
    await _seed_call(sm, "L-1", "no_answer", days_ago=2)
    await _seed_sent(sm, "L-1", days_ago=4)

    r = await client.get("/brokers/L-1")
    assert r.status_code == 200, r.text
    body = r.json()
    broker = body["broker"]
    assert broker["phone"]["source"] == "FMCSA Census"
    assert broker["primary_email"]["source"] == "FMCSA Census"
    assert broker["address"]["value"] == "123 Main St"
    assert broker["address"]["source"] == "Gemini"
    assert len(broker["contacts"]) == 1
    c = broker["contacts"][0]
    assert c["email"]["source"] == "Gemini"
    assert c["is_decision_maker"] is True
    # next_action is one of the four kinds
    assert broker["next_action"]["kind"] in {"call", "email", "follow_up", "wait"}
    # summary
    assert body["summary"]["sent_count_30d"] == 1
    assert body["summary"]["last_call"]["outcome"] == "no_answer"


async def test_detail_404(client: AsyncClient):
    r = await client.get("/brokers/does-not-exist")
    assert r.status_code == 404


async def test_detail_shows_dash_for_missing_fields(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-blank", phone=None, primary_email=None, address=None)
    r = await client.get("/brokers/L-blank")
    body = r.json()["broker"]
    assert body["phone"]["value"] is None
    assert body["primary_email"]["value"] is None
    assert body["address"]["value"] is None


# ---------- /brokers/{id}/activity ----------------------------------------


async def test_activity_paginates(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-act")
    for i in range(6):
        await _seed_call(sm, "L-act", "no_answer", days_ago=i + 1)

    r = await client.get("/brokers/L-act/activity?limit=3")
    body = r.json()
    assert len(body["items"]) == 3
    assert body["next_cursor"] is not None

    r2 = await client.get(f"/brokers/L-act/activity?limit=3&cursor={body['next_cursor']}")
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["items"]) == 3
    # No overlap across pages (timestamps differ).
    first_ts = {x["logged_at"] for x in body["items"]}
    second_ts = {x["logged_at"] for x in body2["items"]}
    assert first_ts.isdisjoint(second_ts)


async def test_detail_rule1_pending_callback(client: AsyncClient):
    """Wire-up: a pending callback_at <= today puts next_action=call."""
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-cb")
    await _seed_call(sm, "L-cb", "callback", days_ago=3, callback_at=datetime.now(UTC).date())

    r = await client.get("/brokers/L-cb")
    assert r.json()["broker"]["next_action"]["kind"] == "call"
    assert "Callback" in r.json()["broker"]["next_action"]["reason"]


# ---------- /brokers/{id}/objections --------------------------------------


async def _seed_suppression(sm, email: str, reason: str = "do_not_contact") -> None:
    from app.models import Suppression

    async with sm() as s:
        s.add(Suppression(email=email, reason=reason))
        await s.commit()


async def test_objections_fires_for_not_interested_with_note(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-obj-ni")
    await _seed_call(sm, "L-obj-ni", "not_interested", days_ago=2, note="Rate too low for lanes.")

    r = await client.get("/brokers/L-obj-ni/objections")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["source"] == "call"
    assert item["text"] == "Rate too low for lanes."


async def test_objections_fires_for_suppression_hit(client: AsyncClient):
    """A suppression row keyed to one of the broker's emails appears, even
    when there is no call_outcome."""
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-obj-sup")
    await _seed_contact(sm, "L-obj-sup", email="dispatch@acme.test", name="Dispatch")
    await _seed_suppression(sm, "dispatch@acme.test")

    r = await client.get("/brokers/L-obj-sup/objections")
    assert r.status_code == 200
    body = r.json()
    assert any(
        i["source"] == "suppression" and "dispatch@acme.test" in i["text"]
        for i in body["items"]
    )


async def test_objections_not_interested_without_note_shows_literal(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-obj-nn")
    await _seed_call(sm, "L-obj-nn", "not_interested", days_ago=1)

    r = await client.get("/brokers/L-obj-nn/objections")
    body = r.json()
    assert body["items"][0]["text"] == "Not interested"


async def test_objections_empty_for_untouched_broker(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-obj-empty")

    r = await client.get("/brokers/L-obj-empty/objections")
    assert r.status_code == 200
    assert r.json() == {"items": []}


async def test_objections_404_for_missing_broker(client: AsyncClient):
    r = await client.get("/brokers/does-not-exist/objections")
    assert r.status_code == 404


async def test_objections_404_for_shipper(client: AsyncClient):
    """Only brokers — a Shipper lead with the same id must 404."""
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-obj-shp", kind="Shipper", name="Shp Co", mc="99")

    r = await client.get("/brokers/L-obj-shp/objections")
    assert r.status_code == 404


async def test_objections_requires_owner(client: AsyncClient):
    """The mount applies the owner-only guard — clear the override to confirm 401."""
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-obj-auth")

    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides.pop(current_user, None)
    try:
        r = await client.get("/brokers/L-obj-auth/objections")
        assert r.status_code in (401, 403)
    finally:
        app.dependency_overrides[current_user] = lambda: UserPrincipal(
            id="u1", email="owner@test", role="owner"
        )


async def test_objections_newest_first_across_sources(client: AsyncClient):
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_lead(sm, id="L-obj-ord")
    await _seed_contact(sm, "L-obj-ord", email="x@acme.test", name="X")
    # Old call, newer suppression — suppression should come first.
    await _seed_call(sm, "L-obj-ord", "not_interested", days_ago=30, note="old")
    await _seed_suppression(sm, "x@acme.test")

    r = await client.get("/brokers/L-obj-ord/objections")
    items = r.json()["items"]
    assert items[0]["source"] == "suppression"
