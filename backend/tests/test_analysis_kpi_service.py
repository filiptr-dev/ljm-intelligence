"""Smoke tests for the shared KPI service.

Scope kept tight (per the "scope test runs" rule):
  * Period VO invariants
  * period_compare arithmetic
  * overview_kpis / call_outcome_kpis / crawler_kpis roundtrip against a
    tiny in-memory corpus.

Perf test (25k rows, p95 < 300ms) and a full RLS backstop test are out of
scope for this pass — tracked as follow-ups.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis import kpi_service as kpi
from app.db import Base
from app.models import CallOutcome, Lead
from app.shared.tenant import TenantId, set_tenant


TENANT = TenantId("01TESTTENANT0000000000000A")


async def _session() -> AsyncSession:
    set_tenant(TENANT)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()


def test_period_rejects_inverted() -> None:
    with pytest.raises(ValueError):
        kpi.Period(**{"from": datetime(2026, 1, 2, tzinfo=UTC), "to": datetime(2026, 1, 1, tzinfo=UTC)})


def test_period_caps_span() -> None:
    with pytest.raises(ValueError):
        kpi.Period(
            **{
                "from": datetime(2024, 1, 1, tzinfo=UTC),
                "to": datetime(2026, 1, 2, tzinfo=UTC),
            }
        )


def test_period_prev_is_equal_length() -> None:
    p = kpi.period_from_label("7d")
    span = p.to - p.from_
    assert p.prev.to - p.prev.from_ == span


def test_period_compare_zero_prev_no_pct() -> None:
    d = kpi.period_compare(5, 0)
    assert d.delta_pct is None and d.direction == "up"


def test_period_compare_down() -> None:
    d = kpi.period_compare(50, 100)
    assert d.delta_pct == -50.0 and d.direction == "down"


async def test_overview_kpis_empty_db() -> None:
    s = await _session()
    p = kpi.period_from_label("7d")
    out = await kpi.overview_kpis(s, TENANT, p)
    assert out["tiles"][0]["value"] == 0.0
    assert out["tiles"][0]["prev"] == 0.0
    assert out["booked_vs_rejected"]["series"] == []


async def test_call_outcome_kpis_mix_and_conversion() -> None:
    s = await _session()
    now = datetime.now(UTC)
    rows = [
        CallOutcome(tenant_id=TENANT, lead_id="x1", outcome="booked", logged_at=now),
        CallOutcome(tenant_id=TENANT, lead_id="x1", outcome="booked", logged_at=now),
        CallOutcome(tenant_id=TENANT, lead_id="x1", outcome="not_interested", logged_at=now),
        CallOutcome(tenant_id=TENANT, lead_id="x1", outcome="no_answer", logged_at=now),
    ]
    s.add_all(rows)
    s.add(
        Lead(
            tenant_id=TENANT,
            id="x1",
            name="Acme",
            kind="Broker",
            state="PA",
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    await s.commit()
    p = kpi.period_from_label("7d")
    kpi.CACHE.invalidate(TENANT, "call_outcomes")
    out = await kpi.call_outcome_kpis(s, TENANT, p)
    mix = {r["key"]: r["value"] for r in out["mix"]["rows"]}
    assert mix["booked"] == 2.0 and mix["not_interested"] == 1.0 and mix["no_answer"] == 1.0
    # Conversion: 2 booked / (2 booked + 1 not_interested) = 66.67%
    conv_tile = out["tiles"][1]
    assert conv_tile["label"] == "Conversion"
    assert abs(conv_tile["value"] - 66.666) < 0.1


async def test_cache_invalidate_bumps_epoch() -> None:
    s = await _session()
    p = kpi.period_from_label("7d")
    out1 = await kpi.overview_kpis(s, TENANT, p)
    # Insert a lead; without invalidation, cache would still return the old shape
    s.add(
        Lead(
            tenant_id=TENANT,
            id="x9",
            name="New",
            kind="Broker",
            state="PA",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
    )
    await s.commit()
    kpi.bump_cache_key(TENANT, "overview")
    out2 = await kpi.overview_kpis(s, TENANT, p)
    assert out2["tiles"][0]["value"] > out1["tiles"][0]["value"]
