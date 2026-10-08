"""Tests for the broker overview-metrics aggregate — pure formula + wired API.

Covers the acceptance criteria:
  AC5 — health formula + thin-flag + clamp (pure, no DB).
  AC6 — ``?include=overview_metrics`` adds the block; without it the response
         shape is byte-identical to the pre-overview payload.
  AC7 — ``GET /brokers/{id}`` always includes ``overview_metrics``.
  AC8 — payment-issues chip fires on bounce OR suppression.
  AC9 — monthly series shape, segment bucketing, thin-flag thresholds,
         no-data broker returns well-formed null-y block.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.deps import UserPrincipal, current_user
from app.db import Base
from app.main import create_app
from app.models import CallOutcome, Lead, LeadContact, Suppression
from app.prospecting.broker_health import (
    THIN_DECIDED,
    HealthInputs,
    compute_health,
)
from app.prospecting.brokers_service import get_overview_metrics

# ---------- pure health formula --------------------------------------------


def test_health_empty_inputs_is_zero_or_renormalised():
    """A broker with no history → score 0, thin flag on, components empty."""
    out = compute_health(
        HealthInputs(
            days_since_last_contact=None,
            booked_12m=0,
            rejected_12m=0,
            avg_sentiment=None,
            sent_30d=0,
        )
    )
    assert 0 <= out.score <= 100
    assert out.thin is True


def test_health_thin_flag_boundary():
    """At THIN_DECIDED-1 decided calls → thin; at THIN_DECIDED exactly → not thin."""
    thin = compute_health(
        HealthInputs(
            days_since_last_contact=0,
            booked_12m=THIN_DECIDED - 1,
            rejected_12m=0,
            avg_sentiment=0.0,
            sent_30d=0,
        )
    )
    not_thin = compute_health(
        HealthInputs(
            days_since_last_contact=0,
            booked_12m=THIN_DECIDED,
            rejected_12m=0,
            avg_sentiment=0.0,
            sent_30d=0,
        )
    )
    assert thin.thin is True
    assert not_thin.thin is False


def test_health_clamps_to_0_100():
    hot = compute_health(
        HealthInputs(
            days_since_last_contact=0,
            booked_12m=20,
            rejected_12m=0,
            avg_sentiment=1.0,
            sent_30d=100,
        )
    )
    assert 0 <= hot.score <= 100
    cold = compute_health(
        HealthInputs(
            days_since_last_contact=1000,
            booked_12m=0,
            rejected_12m=100,
            avg_sentiment=-1.0,
            sent_30d=0,
        )
    )
    assert 0 <= cold.score <= 100
    assert hot.score > cold.score


def test_health_components_sum_close_to_score():
    out = compute_health(
        HealthInputs(
            days_since_last_contact=30,
            booked_12m=6,
            rejected_12m=2,
            avg_sentiment=0.5,
            sent_30d=5,
        )
    )
    assert out.score > 0
    assert abs(sum(out.components.values()) - out.score) < 1.5


# ---------- API test harness ------------------------------------------------


@pytest.fixture
async def client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()
    app.state.sessionmaker = sm
    app.dependency_overrides[current_user] = lambda: UserPrincipal(id="u1", email="o@t", role="owner")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._sm = sm  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


async def _seed_broker(sm, lead_id: str, **kw):
    # MC + primary_email must be unique on `leads` under PG — derive from id.
    unique_mc = str(abs(hash(lead_id)) % 10_000_000)
    defaults = {
        "id": lead_id, "name": f"Broker {lead_id}", "kind": "Broker",
        "state": "NJ", "mc": unique_mc, "phone": "5551234567",
        "primary_email": f"ops+{lead_id.lower()}@{lead_id.lower()}.test",
        "raw": {}, "evidence": {}, "recommendations": [], "fit_score": 70,
    }
    defaults.update(kw)
    async with sm() as s:
        s.add(Lead(**defaults))
        await s.commit()


# ---------- the opt-in rule (AC6) -----------------------------------------


async def test_list_without_include_omits_overview(client):
    await _seed_broker(client._sm, "L-1")
    r = await client.get("/brokers")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0].get("overview") is None
    assert body.get("segments_count") is None


async def test_list_with_include_adds_overview(client):
    await _seed_broker(client._sm, "L-1")
    r = await client.get("/brokers?include=overview_metrics")
    assert r.status_code == 200
    body = r.json()
    row = body["items"][0]
    assert "overview" in row and row["overview"] is not None
    om = row["overview"]
    assert 0 <= om["health_score"] <= 100
    assert isinstance(om["monthly_series"], list)
    assert len(om["monthly_series"]) == 12
    assert om["segment"] in {
        "hot", "warm", "payment_issues", "dormant", "not_interested", "neutral"
    }
    assert body["segments_count"] is not None
    assert body["segments_count"]["all"] >= 1


# ---------- payment-issues wiring (AC8) ------------------------------------


async def test_payment_issues_segment_from_bounce(client):
    await _seed_broker(client._sm, "L-B", primary_email="b@b.test")
    async with client._sm() as s:
        s.add(LeadContact(
            lead_id="L-B", name="X", email="b@b.test",
            source="demo", is_decision_maker=False, pipeline_status="bounced",
        ))
        await s.commit()
    r = await client.get("/brokers?include=overview_metrics")
    body = r.json()
    om = body["items"][0]["overview"]
    assert om["has_bounce"] is True
    assert om["segment"] == "payment_issues"


async def test_payment_issues_segment_from_suppression(client):
    await _seed_broker(client._sm, "L-S", primary_email="opt@out.test")
    async with client._sm() as s:
        s.add(LeadContact(
            lead_id="L-S", name="X", email="opt@out.test",
            source="demo", is_decision_maker=False, pipeline_status="found",
        ))
        s.add(Suppression(email="opt@out.test", reason="unsubscribe"))
        await s.commit()
    r = await client.get("/brokers?include=overview_metrics")
    om = r.json()["items"][0]["overview"]
    assert om["has_suppression"] is True
    assert om["segment"] == "payment_issues"


# ---------- monthly series + win-rate shape (AC9) --------------------------


async def test_monthly_series_counts_booked_and_rejected(client):
    now = datetime.now(UTC)
    await _seed_broker(client._sm, "L-C")
    async with client._sm() as s:
        s.add(CallOutcome(lead_id="L-C", outcome="booked", logged_at=now))
        s.add(CallOutcome(lead_id="L-C", outcome="not_interested", logged_at=now))
        await s.commit()
    r = await client.get("/brokers?include=overview_metrics")
    om = r.json()["items"][0]["overview"]
    assert om["booked_12m"] == 1
    assert om["rejected_12m"] == 1
    # current month bucket is the last point
    last = om["monthly_series"][-1]
    assert last["booked"] == 1
    assert last["rejected"] == 1


async def test_no_data_broker_returns_well_formed_null_block(client):
    await _seed_broker(client._sm, "L-N")
    r = await client.get("/brokers?include=overview_metrics")
    om = r.json()["items"][0]["overview"]
    assert om["health_score"] == 0 or om["health_thin"] is True
    assert om["win_rate"] is None
    assert om["reply_rate"] is None
    assert om["tone_30d"] is None
    assert om["booked_12m"] == 0
    assert len(om["monthly_series"]) == 12


# ---------- segment filter + sort (AC2, AC4) -------------------------------


async def test_segment_filter_narrows_rows(client):
    await _seed_broker(client._sm, "L-A")
    await _seed_broker(client._sm, "L-B", primary_email="b@b.test")
    async with client._sm() as s:
        s.add(LeadContact(
            lead_id="L-B", name="X", email="b@b.test",
            source="demo", is_decision_maker=False, pipeline_status="bounced",
        ))
        await s.commit()
    r = await client.get("/brokers?include=overview_metrics&segment=payment_issues")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == "L-B"


async def test_sort_health_param_round_trips(client):
    await _seed_broker(client._sm, "L-1")
    await _seed_broker(client._sm, "L-2")
    r = await client.get("/brokers?include=overview_metrics&sort=health")
    assert r.status_code == 200


# ---------- detail route includes overview_metrics (AC7) -------------------


async def test_detail_includes_overview_metrics(client):
    await _seed_broker(client._sm, "L-D")
    r = await client.get("/brokers/L-D")
    assert r.status_code == 200
    body = r.json()
    assert "overview_metrics" in body
    assert body["overview_metrics"] is not None
    assert isinstance(body["overview_metrics"]["monthly_series"], list)


# ---------- CSV export (extra 4) -------------------------------------------


async def test_csv_export_streams_segment(client):
    # Seed a formula-injection probe: the exported cell for a broker named
    # "=SUM(1+1)" must be prefixed with a single quote so Excel / Sheets /
    # Numbers render it as text instead of evaluating it.
    await _seed_broker(client._sm, "L-X", name="=SUM(1+1)")
    r = await client.get("/brokers.csv?segment=all")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    import csv as _csv
    import io as _io
    reader = _csv.reader(_io.StringIO(r.text))
    rows = list(reader)
    header = rows[0]
    assert "health_score" in header
    # 23 columns in the exported row (see router _csv.writer header list).
    assert len(header) == 23
    data_rows = [row for row in rows[1:] if row and row[0] == "L-X"]
    assert len(data_rows) == 1
    data = data_rows[0]
    assert len(data) == len(header)
    # name is the 2nd column (index 1); must be the escaped form.
    assert data[1] == "'=SUM(1+1)"


async def test_overview_metrics_second_call_hits_cache(client: AsyncClient):
    """Second call within the TTL must not open a DB session."""
    sm = client._sm  # type: ignore[attr-defined]
    async with sm() as s:
        s.add(Lead(id="L-cache", name="Cache Co", kind="Broker", state="NJ"))
        await s.commit()

    first = await get_overview_metrics(sm, ["L-cache"])
    assert "L-cache" in first

    def _boom():
        raise AssertionError("second call re-queried the DB")

    second = await get_overview_metrics(_boom, ["L-cache"])
    assert second == first


async def test_overview_cache_returns_copy_and_clears_on_booked(client: AsyncClient):
    """Mutating a returned dict must not poison the cache, and a load status
    flip must clear the brokers_overview bucket."""
    from datetime import timedelta as _td

    from app.integrations.loads_service import set_group_status
    from app.models import Load

    sm = client._sm  # type: ignore[attr-defined]
    async with sm() as s:
        s.add(Lead(id="L-copy", name="Copy Co", kind="Broker", state="NJ"))
        s.add(Load(
            source="dat", source_ref="copy-1", broker_name="Copy Co",
            origin_state="VA", dest_state="GA",
            pickup_date=datetime.now(UTC) + _td(days=1), equipment="van",
            rate_usd=1000, miles=100, posted_at=datetime.now(UTC),
            raw={}, status="new", dedupe_group_hash="gh-copy",
        ))
        await s.commit()

    first = await get_overview_metrics(sm, ["L-copy"])
    first.pop("L-copy")  # caller mutation
    second = await get_overview_metrics(sm, ["L-copy"])
    assert "L-copy" in second
    assert second is not first

    # A status write clears the bucket -> next call must re-query the DB.
    assert await set_group_status(sm, "gh-copy", "booked") == 1

    def _boom():
        raise AssertionError("re-queried after invalidation")

    with pytest.raises(AssertionError):
        await get_overview_metrics(_boom, ["L-copy"])
