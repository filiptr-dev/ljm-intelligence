"""Round trip per outcome: POST /outcome -> history + list read-back match what was picked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.models import CallOutcome
from tests.test_call_list_api import _seed_lead, client  # noqa: F401

OUTCOMES = ["booked", "callback", "not_interested", "no_answer"]


@pytest.mark.parametrize("kind", OUTCOMES)
async def test_outcome_round_trip(client, kind):  # noqa: F811
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    await _seed_lead(sm, id="MC-1")

    r = await client.post("/tools/call-list/outcome", json={"lead_id": "MC-1", "outcome": kind})
    assert r.status_code == 200, r.text

    hist = (await client.get("/tools/call-list/history?lead_id=MC-1")).json()["items"]
    assert [h["outcome"] for h in hist] == [kind]

    # Age the row so the lead is eligible again (callbacks come due, one-call-a-day guard lapses)
    # and read the list back: last_outcome must be exactly what was picked.
    async with sm() as s:
        await s.execute(
            update(CallOutcome).values(
                logged_at=datetime.now(UTC) - timedelta(days=20),
                callback_at=(datetime.now(UTC) - timedelta(days=1)).date() if kind == "callback" else None,
            )
        )
        await s.commit()
    items = (await client.get("/tools/call-list")).json()["items"]
    if kind in ("booked", "not_interested"):
        # Booked >14d ago is eligible again; not_interested is suppressed forever.
        assert ("MC-1" in [i["lead_id"] for i in items]) == (kind == "booked")
    for i in items:
        if i["lead_id"] == "MC-1":
            assert i["last_outcome"]["outcome"] == kind


def test_evening_et_outcome_counts_as_today():
    """9pm ET is already tomorrow in UTC; the one-call-a-day guard must use the ET day."""
    from datetime import date

    from app.models import Lead
    from app.prospecting.pipeline.call_rank import rank_call_list

    lead = Lead(id="MC-1", name="A", kind="Broker", state="NJ", phone="5551234567", current_score=80, raw={})
    today = date(2026, 10, 8)
    logged = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)  # 21:00 ET on Oct 8
    o = CallOutcome(lead_id="MC-1", outcome="no_answer", logged_at=logged)
    assert rank_call_list([lead], [o], [], today) == []
