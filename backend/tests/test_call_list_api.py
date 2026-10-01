"""Call List API — happy path + suppression + callback pin + validation.

DB choice: in-memory SQLite via `aiosqlite`. Why not Neon like `test_health.py`?
Because these tests INSERT `call_outcomes` and read the ranked list back — writing
test rows into the shared prod Neon DB would poison the daily queue for the
operator and every other developer. Our models already declare a JSON→SQLite
variant (`JSONType = JSONB().with_variant(JSON(), "sqlite")`) and the partial
indexes use `postgresql_where` (silently ignored on SQLite), so the same
`Base.metadata.create_all` gives us a clean fresh schema per test.

We skip the app's real lifespan (which would spin up a Neon engine) and instead
override `app.state.sessionmaker` directly — the routes only ever reach for the
sessionmaker, never the engine, so that's all we need.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import CallOutcome, Lead

TODAY = datetime.now(UTC).date()  # routes use "now" — align the fixture with wall-clock today


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
    app.state.sessionmaker = sessionmaker  # skip Neon lifespan; hand-wire the DB.

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Expose the sessionmaker on the client for tests that need to seed / assert directly.
        c._test_sessionmaker = sessionmaker  # type: ignore[attr-defined]
        yield c

    await engine.dispose()


async def _seed_lead(sm, **kw) -> Lead:
    lead_id = kw.get("id", "MC-1")
    defaults = {
        "id": lead_id,
        # Derive MC from the id so uniqueness holds across many seeded rows without
        # every test having to spell one out.
        "mc": lead_id.replace("MC-", "") or None,
        "name": "Acme Broker LLC",
        "kind": "Broker",
        "state": "NJ",
        "city": "Newark",
        "phone": "5551234567",
        "primary_email": "hi@acme.example",
        "current_score": 80,
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


async def _seed_outcome(sm, **kw) -> CallOutcome:
    async with sm() as s:
        row = CallOutcome(**kw)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row


# ---------------------------------------------------------------------------
# GET /tools/call-list — happy path
# ---------------------------------------------------------------------------


async def test_get_call_list_returns_ranked_rows_with_shape(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-HOT", name="Hot Broker", current_score=100)
    await _seed_lead(sm, id="MC-COLD", name="Cold Broker", current_score=10)
    # No phone → drops out.
    await _seed_lead(sm, id="MC-NOPHONE", name="No Phone LLC", phone=None)

    r = await client.get("/tools/call-list")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == TODAY.isoformat()
    ids = [row["lead_id"] for row in body["items"]]
    assert "MC-HOT" in ids and "MC-COLD" in ids
    assert "MC-NOPHONE" not in ids  # phone-required drop
    # Hot before cold — deterministic order.
    assert ids.index("MC-HOT") < ids.index("MC-COLD")
    # Row shape contract.
    hot = next(row for row in body["items"] if row["lead_id"] == "MC-HOT")
    assert hot["phone"]
    assert hot["opener"]
    assert isinstance(hot["reasons"], list)


async def test_get_call_list_respects_limit(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    for i in range(30):
        await _seed_lead(sm, id=f"MC-{i:03d}", name=f"Broker {i}", current_score=50)

    r = await client.get("/tools/call-list?limit=10")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 10


# ---------------------------------------------------------------------------
# POST /tools/call-list/outcome
# ---------------------------------------------------------------------------


async def test_post_outcome_not_interested_drops_lead_from_list(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-1", current_score=100)
    await _seed_lead(sm, id="MC-2", current_score=90)

    # Before: both present.
    r = await client.get("/tools/call-list")
    ids = [row["lead_id"] for row in r.json()["items"]]
    assert {"MC-1", "MC-2"} <= set(ids)

    r = await client.post(
        "/tools/call-list/outcome",
        json={"lead_id": "MC-1", "outcome": "not_interested"},
    )
    assert r.status_code == 200, r.text
    ids = [row["lead_id"] for row in r.json()["items"]]
    assert "MC-1" not in ids  # suppressed forever
    assert "MC-2" in ids


async def test_post_outcome_called_today_drops_lead(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-1", current_score=100)

    r = await client.post(
        "/tools/call-list/outcome",
        json={"lead_id": "MC-1", "outcome": "no_answer"},
    )
    assert r.status_code == 200
    ids = [row["lead_id"] for row in r.json()["items"]]
    assert "MC-1" not in ids  # one-call-per-day


async def test_post_outcome_callback_defaults_to_plus_two_days(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-1")

    r = await client.post(
        "/tools/call-list/outcome",
        json={"lead_id": "MC-1", "outcome": "callback"},
    )
    assert r.status_code == 200

    # Read history to confirm callback_at defaulted correctly.
    h = await client.get("/tools/call-list/history?lead_id=MC-1")
    assert h.status_code == 200
    items = h.json()["items"]
    assert len(items) == 1
    assert items[0]["callback_at"] == (TODAY + timedelta(days=2)).isoformat()


async def test_post_outcome_unknown_lead_returns_404(client: AsyncClient):
    r = await client.post(
        "/tools/call-list/outcome",
        json={"lead_id": "MC-DOES-NOT-EXIST", "outcome": "booked"},
    )
    assert r.status_code == 404


async def test_post_outcome_invalid_enum_is_422(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-1")

    r = await client.post(
        "/tools/call-list/outcome",
        json={"lead_id": "MC-1", "outcome": "maybe"},
    )
    assert r.status_code == 422


async def test_post_outcome_missing_lead_id_is_422(client: AsyncClient):
    r = await client.post("/tools/call-list/outcome", json={"outcome": "booked"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Callback pin: rescheduled to today → floats to the top of the list.
# ---------------------------------------------------------------------------


async def test_callback_scheduled_for_today_returns_at_top(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    # A hot lead that would otherwise win on merit.
    await _seed_lead(sm, id="MC-HOT", current_score=100)
    # A pinned callback for today — the ranker gives this +50, should beat hot.
    await _seed_lead(sm, id="MC-PIN", current_score=0, primary_email=None)
    await _seed_outcome(
        sm,
        lead_id="MC-PIN",
        outcome="callback",
        callback_at=TODAY,
        logged_at=datetime.now(UTC) - timedelta(days=3),
    )

    r = await client.get("/tools/call-list")
    assert r.status_code == 200
    ids = [row["lead_id"] for row in r.json()["items"]]
    assert ids[0] == "MC-PIN", f"expected pinned callback at top, got {ids[:3]}"


async def test_callback_scheduled_in_future_drops(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-1")
    await _seed_outcome(
        sm,
        lead_id="MC-1",
        outcome="callback",
        callback_at=TODAY + timedelta(days=5),
        logged_at=datetime.now(UTC) - timedelta(days=1),
    )
    r = await client.get("/tools/call-list")
    ids = [row["lead_id"] for row in r.json()["items"]]
    assert "MC-1" not in ids


# ---------------------------------------------------------------------------
# GET /tools/call-list/history
# ---------------------------------------------------------------------------


async def test_history_returns_lead_timeline_newest_first(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-1")
    older = datetime.now(UTC) - timedelta(days=5)
    newer = datetime.now(UTC) - timedelta(days=1)
    await _seed_outcome(sm, lead_id="MC-1", outcome="no_answer", logged_at=older)
    await _seed_outcome(sm, lead_id="MC-1", outcome="callback", callback_at=TODAY + timedelta(days=2), logged_at=newer)

    r = await client.get("/tools/call-list/history?lead_id=MC-1")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["outcome"] for i in items] == ["callback", "no_answer"]


async def test_history_unknown_lead_returns_404(client: AsyncClient):
    r = await client.get("/tools/call-list/history?lead_id=MC-DOES-NOT-EXIST")
    assert r.status_code == 404


async def test_history_missing_lead_id_is_422(client: AsyncClient):
    r = await client.get("/tools/call-list/history")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /outcome returns the new id; DELETE /outcome/{id} truly undoes
# ---------------------------------------------------------------------------


async def test_post_outcome_returns_logged_outcome_id(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-UNDO-1")
    r = await client.post(
        "/tools/call-list/outcome",
        json={"lead_id": "MC-UNDO-1", "outcome": "no_answer"},
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("logged_outcome_id"), int) and body["logged_outcome_id"] >= 1


async def test_delete_outcome_undoes_the_row(client: AsyncClient):
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-UNDO-2")
    posted = await client.post(
        "/tools/call-list/outcome",
        json={"lead_id": "MC-UNDO-2", "outcome": "booked"},
    )
    oid = posted.json()["logged_outcome_id"]

    r = await client.delete(f"/tools/call-list/outcome/{oid}")
    assert r.status_code == 200
    # Second DELETE on the same id → 404 (idempotent-from-caller: nothing to undo).
    r2 = await client.delete(f"/tools/call-list/outcome/{oid}")
    assert r2.status_code == 404

    # And the history for the lead is empty again.
    hist = await client.get("/tools/call-list/history?lead_id=MC-UNDO-2")
    assert hist.status_code == 200 and hist.json()["items"] == []


async def test_outcomes_query_is_bounded_by_lookback(client: AsyncClient, monkeypatch):
    """A very old outcome outside the 90-day window must not reach the ranker."""
    from app.prospecting import call_list_service as svc

    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-OLD")
    # 400 days ago — comfortably outside the 90d lookback.
    await _seed_outcome(
        sm,
        lead_id="MC-OLD",
        outcome="booked",
        logged_at=datetime.now(UTC) - timedelta(days=400),
    )

    rows = await svc.load_and_rank(sm, TODAY, 25)
    # Ancient booked rows must not keep the lead off today's list via the
    # suppression rule — if the bound is working, this lead appears.
    assert any(r.lead_id == "MC-OLD" for r in rows)


def test_today_et_follows_eastern_timezone():
    """At 23:59 UTC on day D, ET is already 19:59 D (EST) or 19:59 D (EDT) — still D."""
    # Smoke: calling it works and returns a date in the operator-local day.
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Z

    from app.prospecting.call_list_service import today_et

    assert today_et() == _dt.now(_Z("America/New_York")).date()
