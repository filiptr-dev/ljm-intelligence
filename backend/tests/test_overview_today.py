"""Overview /overview/today — aggregate read, deterministic shape.

Same fixture pattern as ``test_call_list_api.py``: an in-memory SQLite DB with
``Base.metadata.create_all`` and the app's sessionmaker swapped at
``app.state.sessionmaker``, so no Neon round-trip and no cross-test leakage.

Owner-only is covered by ``test_call_list_api.py``'s shared ``current_user``
dep (identical mount — ``user_only``). The last test here opts out of the
test conftest's ``auth_bypass`` to assert the 401 wiring is intact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import CallOutcome, CapacityPost, CrawlRun, Lead

ET = ZoneInfo("America/New_York")
TODAY_ET = datetime.now(tz=ET).date()


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


def _lead(
    i: int,
    *,
    name: str,
    state: str = "NJ",
    phone: str | None = "212-555-0100",
    first_seen_at: datetime | None = None,
    kind: str = "Broker",
    current_score: int | None = None,
) -> Lead:
    return Lead(
        id=f"MC-{i:04d}",
        mc=str(1000 + i),
        name=name,
        kind=kind,
        state=state,
        phone=phone,
        current_score=current_score,
        first_seen_at=first_seen_at or datetime.now(UTC) - timedelta(days=120),
    )


async def _seed(client: AsyncClient, *, leads=(), outcomes=(), posts=(), crawls=()) -> None:
    sm = client._test_sessionmaker  # type: ignore[attr-defined]
    async with sm() as s:
        # Leads first: `call_outcomes.lead_id` has a FK to `leads.id` that PG16
        # enforces (sqlite doesn't). Flush before the dependents.
        for row in leads:
            s.add(row)
        if leads:
            await s.flush()
        for row in (*outcomes, *posts, *crawls):
            s.add(row)
        await s.commit()


# ---------------------------------------------------------------------------
# Core shape + deterministic merge
# ---------------------------------------------------------------------------


async def test_shape_and_deterministic_merge(client: AsyncClient) -> None:
    """Three fixture rows → one call, one capacity_match, one new_lead row."""
    # Finished crawl 1 hour ago → the "new lead" threshold is that timestamp.
    crawl_at = datetime.now(UTC) - timedelta(hours=1)
    crawl = CrawlRun(
        id="run-1",
        started_at=crawl_at - timedelta(minutes=10),
        finished_at=crawl_at,
        status="done",
        kind="fmcsa",
        counts={},
        trigger="cron",
    )

    # Call-list candidate (phone, NJ, high current_score → high ranked score).
    call_lead = _lead(1, name="Call Co", current_score=90)
    # Capacity-match candidate: same origin_state NJ. No phone → doesn't
    # enter the call-list (we want exactly one call row), but the broker-fit
    # scorer doesn't require phone so it still surfaces as capacity_match.
    cap_lead = _lead(2, name="Match Co", state="NJ", phone=None, current_score=20)
    # New lead crawled after the finished crawl.
    new_lead = _lead(
        3,
        name="Fresh Co",
        state="PA",
        phone=None,  # no phone → not in the call list, still eligible as new-lead
        first_seen_at=datetime.now(UTC),
    )

    post = CapacityPost(
        id="post-1",
        kind="truck",
        equipment="Dry Van",
        origin_state="NJ",
        destinations=["PA"],
        status="open",
    )

    await _seed(client, leads=(call_lead, cap_lead, new_lead), posts=(post,), crawls=(crawl,))

    r = await client.get("/overview/today")
    assert r.status_code == 200, r.text
    body = r.json()

    # Shape — top-level keys all present.
    assert set(body.keys()) == {"date", "tiles", "do_next", "booked_vs_rejected", "header"}
    assert body["date"] == TODAY_ET.isoformat()
    assert body["header"] == {"crawl_now_href": "/leads", "new_post_href": "/capacity?new=1"}

    # Tiles present with right keys.
    tiles = body["tiles"]
    assert set(tiles.keys()) == {
        "to_call_today",
        "new_leads_since_last_crawl",
        "loads_booked_90d",
    }
    assert tiles["new_leads_since_last_crawl"]["since"] is not None
    assert tiles["new_leads_since_last_crawl"]["value"] == 1  # Fresh Co

    # do_next ordering: call priority (3) → capacity_match (2) → new_lead (1).
    kinds = [row["kind"] for row in body["do_next"]]
    assert kinds == ["call", "capacity_match", "new_lead"], kinds


async def test_to_call_today_matches_ranker(client: AsyncClient) -> None:
    """``tiles.to_call_today`` equals the full ranker's output length."""
    from app.prospecting.pipeline.call_rank import rank_call_list

    leads = [_lead(i, name=f"Lead {i}", current_score=50 + i) for i in range(1, 8)]
    await _seed(client, leads=leads)

    # Compute the expected ranker length on the same inputs.
    expected = len(rank_call_list(leads, [], [], TODAY_ET, top_n=100))

    r = await client.get("/overview/today")
    assert r.status_code == 200
    assert r.json()["tiles"]["to_call_today"]["value"] == expected


# ---------------------------------------------------------------------------
# New-leads fallback (no crawl ever finished)
# ---------------------------------------------------------------------------


async def test_new_leads_falls_back_to_today_et_when_no_crawl(client: AsyncClient) -> None:
    """With no completed crawl, counts leads whose first_seen_at >= ET midnight.

    Crosses the UTC/ET boundary: a lead first_seen at 02:00 UTC today is still
    "yesterday" in ET (ET is UTC-4 or -5), so it must NOT be counted. A lead at
    the ET-local 10am slot (14:00/15:00 UTC) counts as today.
    """
    now_et = datetime.now(tz=ET)
    et_midnight = datetime(now_et.year, now_et.month, now_et.day, tzinfo=ET)

    # 10:00 ET today — unambiguously "today ET".
    today_et_10am = (et_midnight + timedelta(hours=10)).astimezone(UTC)
    # 30 minutes BEFORE ET midnight — unambiguously "yesterday ET".
    yesterday_et_near_mid = (et_midnight - timedelta(minutes=30)).astimezone(UTC)

    today_lead = _lead(1, name="Today Co", first_seen_at=today_et_10am)
    yesterday_lead = _lead(2, name="Yesterday Co", first_seen_at=yesterday_et_near_mid)

    await _seed(client, leads=(today_lead, yesterday_lead))

    r = await client.get("/overview/today")
    assert r.status_code == 200
    tile = r.json()["tiles"]["new_leads_since_last_crawl"]
    assert tile["since"] is None  # "no crawl yet" marker
    assert tile["value"] == 1  # only today_lead


# ---------------------------------------------------------------------------
# Loads booked / booked_vs_rejected demo flag
# ---------------------------------------------------------------------------


async def test_booked_vs_rejected_demo_flag_below_floor(client: AsyncClient) -> None:
    lead = _lead(1, name="Only Co")
    now = datetime.now(UTC)
    outcomes = [
        CallOutcome(lead_id=lead.id, outcome="booked", logged_at=now - timedelta(days=5)),
        CallOutcome(lead_id=lead.id, outcome="not_interested", logged_at=now - timedelta(days=7)),
    ]
    await _seed(client, leads=(lead,), outcomes=outcomes)

    body = (await client.get("/overview/today")).json()
    assert body["tiles"]["loads_booked_90d"]["demo"] is True
    assert body["booked_vs_rejected"]["demo"] is True
    assert body["booked_vs_rejected"]["series"] == []  # empty when demo


async def test_booked_vs_rejected_real_series_when_above_floor(client: AsyncClient) -> None:
    lead = _lead(1, name="Busy Co")
    now = datetime.now(UTC)
    # 12 outcomes split across three months in the last 90d.
    outcomes = []
    for i in range(6):
        outcomes.append(CallOutcome(lead_id=lead.id, outcome="booked", logged_at=now - timedelta(days=5 + i)))
    for i in range(6):
        outcomes.append(
            CallOutcome(
                lead_id=lead.id,
                outcome="not_interested",
                logged_at=now - timedelta(days=35 + i),
            )
        )

    await _seed(client, leads=(lead,), outcomes=outcomes)

    body = (await client.get("/overview/today")).json()
    assert body["tiles"]["loads_booked_90d"]["value"] == 6
    assert body["tiles"]["loads_booked_90d"]["demo"] is False
    assert body["booked_vs_rejected"]["demo"] is False
    assert len(body["booked_vs_rejected"]["series"]) >= 1
    totals = sum(p["booked"] + p["rejected"] for p in body["booked_vs_rejected"]["series"])
    assert totals == 12


# ---------------------------------------------------------------------------
# Determinism — two consecutive calls return byte-identical JSON
# ---------------------------------------------------------------------------


async def test_two_consecutive_calls_are_byte_identical(client: AsyncClient) -> None:
    leads = [_lead(i, name=f"Lead {i}", current_score=40 + i) for i in range(1, 5)]
    await _seed(client, leads=leads)

    r1 = await client.get("/overview/today")
    r2 = await client.get("/overview/today")
    assert r1.status_code == r2.status_code == 200
    assert r1.content == r2.content


# ---------------------------------------------------------------------------
# Owner-only (opt out of the conftest auth bypass)
# ---------------------------------------------------------------------------


async def test_requires_auth(disable_auth_bypass) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()
    disable_auth_bypass(app)
    app.state.sessionmaker = sessionmaker

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/overview/today")
    assert r.status_code in (401, 403)
