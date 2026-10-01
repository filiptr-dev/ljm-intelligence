"""Perf benchmark — list-endpoint p50/p95 on PG16 with 25k seeded leads.

Opt-in, slow, marked ``perf``. Default ``pytest`` runs skip this (see
``addopts = -m 'not perf'``); run explicitly with::

    TEST_HARNESS=pg16 uv run pytest tests/test_perf_list_endpoints.py -m perf -s

The architecture plan calls for a 25k-leads-per-tenant perf test as the
measurement input before any keyset-pagination work. Keyset itself needs a
user decision on sort semantics (deterministic ordering flips the current
behaviour on ``/brokers`` and friends), so this test only MEASURES — it does
not change endpoint shape. The numbers in the dossier closeout drive that
future call.

Measurements per endpoint:
  * p50  — median wall-clock of 20 cold+warm hits
  * p95  — 95th percentile of the same 20

Endpoints measured (all authenticated owner-only reads):
  GET /brokers         — ranked list with contact summary + next-action chip
  GET /call-list       — ranked call list
  GET /overview/today  — operator Today desk aggregate
  GET /shipper_finder  — shipper-finder page

Nothing is asserted; the test is informational. The ``print`` is the
deliverable — the invoking agent / human captures the stdout.
"""

from __future__ import annotations

import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("TEST_HARNESS", "").lower() != "pg16",
        reason="perf test requires TEST_HARNESS=pg16 (needs real Postgres)",
    ),
    pytest.mark.asyncio,
]

_SEED_COUNT = 25_000
_SAMPLE_PER_ENDPOINT = 20


def _ulid(prefix: str, n: int) -> str:
    # Deterministic 26-char ULID-shaped id so repeat runs are stable.
    return (prefix + f"{n:020d}")[:26]


async def _seed_leads(sessionmaker, tenant_id: str, count: int) -> None:
    """Bulk-insert ``count`` leads via raw SQL for speed."""
    import psycopg
    from app.config import Settings

    # Direct psycopg for a COPY-style bulk insert — SQLAlchemy ORM at 25k rows
    # would take minutes. We're measuring query latency, not insert latency.
    sync_url = Settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    now = datetime.now(UTC)
    rows = []
    for i in range(count):
        rows.append(
            (
                _ulid("PERF", i),
                "Perf Broker " + str(i),
                "Broker",
                ["IL", "CA", "TX", "NJ", "GA", "WA"][i % 6],
                str(1_000_000 + i),  # mc
                tenant_id,
                now - timedelta(minutes=i),  # last_seen_at: spread so ORDER BY has work
                now - timedelta(days=1, minutes=i),  # first_seen_at
                40 + (i % 60),  # current_score 40..99
                "{}",  # raw json
                "{}",  # evidence json
                "[]",  # recommendations json
                50 + (i % 50),  # fit_score
            )
        )
    with psycopg.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute("SET session_replication_role = 'replica'")
        with cur.copy(
            "COPY leads (id, name, kind, state, mc, tenant_id, last_seen_at, "
            "first_seen_at, current_score, raw, evidence, recommendations, fit_score) "
            "FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
        conn.commit()


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — small-n safe."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100 * (len(s) - 1)))))
    return s[k]


async def test_perf_list_endpoints_25k_leads(auth_bypass):
    """Seed 25k leads for LJM and measure p50/p95 on the four list endpoints."""
    from app.config import Settings
    from app.db import create_sessionmaker
    from app.main import create_app
    from app.shared.orm import LJM_TENANT_ID
    from sqlalchemy.ext.asyncio import create_async_engine

    s = Settings()
    engine = create_async_engine(s.database_url)
    sessionmaker = create_sessionmaker(engine)

    await _seed_leads(sessionmaker, LJM_TENANT_ID, _SEED_COUNT)

    app = create_app(s)
    auth_bypass(app)
    app.state.sessionmaker = sessionmaker
    app.state.settings = s

    paths = [
        "/brokers",
        "/tools/call-list",
        "/overview/today",
        "/tools/shipper-finder",
    ]

    transport = ASGITransport(app=app)
    results: dict[str, dict[str, float]] = {}
    async with AsyncClient(transport=transport, base_url="http://perf") as c:
        # One warmup per endpoint to prime caches / plans.
        for path in paths:
            r = await c.get(path)
            assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"

        for path in paths:
            samples: list[float] = []
            for _ in range(_SAMPLE_PER_ENDPOINT):
                t0 = time.perf_counter()
                r = await c.get(path)
                dt_ms = (time.perf_counter() - t0) * 1000
                assert r.status_code == 200, f"{path} degraded mid-run: {r.status_code}"
                samples.append(dt_ms)
            results[path] = {
                "p50_ms": _percentile(samples, 50),
                "p95_ms": _percentile(samples, 95),
                "min_ms": min(samples),
                "max_ms": max(samples),
                "n": len(samples),
            }

    await engine.dispose()

    # Stdout the deliverable — captured by the invoking agent / human.
    print("\n=== PERF: 25k leads on PG16 ===")
    print(f"seeded_leads={_SEED_COUNT} tenant={LJM_TENANT_ID}")
    for path, m in results.items():
        print(
            f"  {path:<22} p50={m['p50_ms']:>7.1f}ms  "
            f"p95={m['p95_ms']:>7.1f}ms  "
            f"min={m['min_ms']:>6.1f}  max={m['max_ms']:>6.1f}  n={m['n']}"
        )
