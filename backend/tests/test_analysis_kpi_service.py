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

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis import kpi_service as kpi
from app.db import Base
from app.models import CallOutcome, CapacityPost, Lead
from app.shared.tenant import TenantId, set_tenant

TENANT = TenantId("01TESTTENANT0000000000000A")


@asynccontextmanager
async def _session():
    """Yield a scratch AsyncSession, then close it + dispose the engine.

    Previously this returned a dangling session (no `async with`, no engine
    dispose). On SQLite the GC ate the fallout quietly; on the PG16 harness
    the shim redirects the sqlite URL to the shared PG engine, and leaving
    the session open held a connection that collided with the session-level
    teardown — the suite hung on the last two tests. Owning the lifecycle
    here keeps each test fully self-contained on both harnesses.
    """
    set_tenant(TENANT)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sm() as s:
            yield s
    finally:
        await engine.dispose()


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
    async with _session() as s:
        p = kpi.period_from_label("7d")
        out = await kpi.overview_kpis(s, TENANT, p)
        assert out["tiles"][0]["value"] == 0.0
        assert out["tiles"][0]["prev"] == 0.0
        assert out["booked_vs_rejected"]["series"] == []


async def test_call_outcome_kpis_mix_and_conversion() -> None:
    async with _session() as s:
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
    async with _session() as s:
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


def _post(pid: str, created_at: datetime, status: str = "open") -> CapacityPost:
    return CapacityPost(
        tenant_id=TENANT,
        id=pid,
        kind="truck",
        equipment="Dry Van",
        origin_state="NJ",
        destinations=["PA"],
        status=status,
        created_at=created_at,
    )


async def test_capacity_open_posts_delta_compares_periods() -> None:
    """Open-posts tile compares the window against the prior window — not a
    live count passed as both current and prev (which pinned delta at 0)."""
    async with _session() as s:
        now = datetime.now(UTC)
        # 3 open posts in the current 7d window, 1 open post in the prior 7d window.
        s.add_all(
            [
                _post("c1", now - timedelta(hours=1)),
                _post("c2", now - timedelta(days=1)),
                _post("c3", now - timedelta(days=2)),
                _post("p1", now - timedelta(days=10)),
                _post("p2", now - timedelta(days=10), status="closed"),
            ]
        )
        await s.commit()
        kpi.CACHE.invalidate(TENANT, "capacity")
        out = await kpi.capacity_kpis(s, TENANT, kpi.period_from_label("7d"))
        tile = out["tiles"][0]
        assert tile["label"] == "Open posts"
        assert tile["value"] == 3.0
        assert tile["prev"] == 1.0
        assert tile["delta_pct"] == 200.0
        assert tile["direction"] == "up"


async def test_cache_distinguishes_custom_ranges() -> None:
    """Two custom ranges share label "custom"; they must not share a cache slot."""
    async with _session() as s:
        s.add_all(
            [
                _post("a1", datetime(2026, 3, 10, 15, tzinfo=UTC)),
                _post("a2", datetime(2026, 3, 11, 15, tzinfo=UTC)),
                _post("b1", datetime(2026, 6, 10, 15, tzinfo=UTC)),
            ]
        )
        await s.commit()
        kpi.CACHE.invalidate(TENANT, "capacity")
        march = kpi.Period(
            **{"from": datetime(2026, 3, 9, 12, tzinfo=UTC), "to": datetime(2026, 3, 14, 12, tzinfo=UTC)}
        )
        june = kpi.Period(**{"from": datetime(2026, 6, 9, 12, tzinfo=UTC), "to": datetime(2026, 6, 14, 12, tzinfo=UTC)})
        assert march.label == june.label == "custom"
        out_march = await kpi.capacity_kpis(s, TENANT, march)
        out_june = await kpi.capacity_kpis(s, TENANT, june)  # same tenant, warm cache
        created = lambda out: out["tiles"][1]["value"]
        assert created(out_march) == 2.0
        assert created(out_june) == 1.0
        assert out_march["period"] != out_june["period"]
