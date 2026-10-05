"""Broker-rank SQL parity + keyset + filter tests — PG16 only.

Why PG16 only: the SQL ranker in ``app.prospecting.broker_rank_service`` uses
``LEFT JOIN LATERAL`` which SQLite does not implement. The service has a
sqlite-only fallback for the test harness, but these tests exercise the SQL
path itself — they only make sense against Postgres.

What's here:

* ``test_sql_order_matches_python_ranker`` — AC2: seed ~500 brokers covering
  every rule branch, compare the SQL ranker's output to the pre-existing
  Python ranker item-by-item on ``(id, kind, reason)``.
* ``test_page_walk_concat_equals_single_page`` — AC3: walk every page at
  ``limit=37`` across a 1k fixture; assert the concatenation equals the
  single ``limit=1000`` call and that no row appears twice / is skipped.
* ``test_filter_and_null_fit_ordering`` — AC4: filter ``next_action=call``
  + ``has_phone=true`` + ``min_fit=0``; assert the resulting set + order
  matches the Python path and null-fit rows strictly follow real-fit rows.
"""

from __future__ import annotations

import os
import random
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import CallOutcome, Lead, LeadContact, SentLog, Suppression
from app.prospecting.broker_rank_service import (
    RankFilters,
    rank_brokers,
)
from app.prospecting.brokers_service import list_brokers as py_list_brokers

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("TEST_HARNESS", "").lower() != "pg16",
        reason="broker-rank SQL path requires PG16 (LEFT JOIN LATERAL + partial indexes)",
    ),
    pytest.mark.asyncio,
]


# ---------- shared engine / seed helpers ----------------------------------


@pytest.fixture
async def sm():
    """One engine/sessionmaker per test. The conftest redirects the sqlite
    URL to the shared PG16 test DB and truncates between tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    # On PG16 the schema already exists (alembic upgrade head in conftest);
    # the redirect makes create_all a no-op there. Only run it on sqlite —
    # this test module is PG16-only via pytestmark, so this branch is
    # effectively dead but keeps the fixture importable under sqlite too.
    if engine.dialect.name != "postgresql":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    yield sessionmaker
    await engine.dispose()


_NOW = datetime.now(UTC)
_TODAY: date = _NOW.date()


async def _seed_broker(
    sm,
    id_: str,
    *,
    name: str = "Broker",
    state: str = "NJ",
    fit_score: int | None = 50,
    phone: str | None = "5551234567",
    primary_email: str | None = None,
) -> None:
    async with sm() as s:
        s.add(
            Lead(
                id=id_, name=name, kind="Broker", state=state, mc=id_,
                phone=phone, primary_email=primary_email, fit_score=fit_score,
                raw={}, evidence={}, recommendations=[],
            )
        )
        await s.commit()


async def _seed_call(sm, lead_id: str, outcome: str, days_ago: int,
                     callback_at: date | None = None) -> None:
    async with sm() as s:
        s.add(
            CallOutcome(
                lead_id=lead_id, outcome=outcome,
                logged_at=_NOW - timedelta(days=days_ago),
                callback_at=callback_at,
            )
        )
        await s.commit()


async def _seed_sent(sm, lead_id: str, days_ago: int, replied: bool = False) -> None:
    sent_at = _NOW - timedelta(days=days_ago)
    async with sm() as s:
        s.add(
            SentLog(
                lead_id=lead_id, mode="simulated", to_email="x@x.test",
                subject="hi", sent_at=sent_at,
                replied_at=sent_at + timedelta(hours=1) if replied else None,
            )
        )
        await s.commit()


async def _seed_contact_email(sm, lead_id: str, email: str,
                              pipeline_status: str = "found") -> None:
    async with sm() as s:
        s.add(
            LeadContact(
                lead_id=lead_id, name="C", email=email, phone=None,
                source="test", is_decision_maker=False,
                pipeline_status=pipeline_status,
            )
        )
        await s.commit()


# ---------- parity --------------------------------------------------------


async def _seed_rule_coverage_fixture(sm, n_per_bucket: int = 40) -> int:
    """Seed ~400 brokers across every rule branch + a long tail of
    rule-10 (dormant) fillers to push total close to 500. Returns total
    broker count seeded.
    """
    i = 0

    def nid() -> str:
        nonlocal i
        i += 1
        return f"L{i:04d}"

    # Bucket 1 — rule 1 (callback-due, pending today or earlier).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"CB {lid}", fit_score=random.randint(0, 100))
        await _seed_call(sm, lid, "callback", days_ago=3, callback_at=_TODAY)

    # Bucket 2 — rule 2 (booked within 30d, call newest).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"BK {lid}", fit_score=random.randint(0, 100))
        await _seed_call(sm, lid, "booked", days_ago=10)

    # Bucket 3 — rule 3 (not_interested within 90d, call newest).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"NI {lid}", fit_score=random.randint(0, 100))
        await _seed_call(sm, lid, "not_interested", days_ago=40)

    # Bucket 4 — rule 4 (emailed <=7d, no reply).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"E7 {lid}", fit_score=random.randint(0, 100))
        await _seed_sent(sm, lid, days_ago=3, replied=False)

    # Bucket 5 — rule 5 (replied within 30d).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"RP {lid}", fit_score=random.randint(0, 100))
        await _seed_sent(sm, lid, days_ago=5, replied=True)

    # Bucket 6 — rule 6 (7–21d since email, no reply, sendable email).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"NU {lid}", fit_score=random.randint(0, 100))
        await _seed_sent(sm, lid, days_ago=14, replied=False)
        await _seed_contact_email(sm, lid, f"c-{lid}@x.test")

    # Bucket 7 — rule 7 (no_answer within 7d, has phone).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"NA {lid}", fit_score=random.randint(0, 100))
        await _seed_call(sm, lid, "no_answer", days_ago=3)

    # Bucket 8 — rule 8 (phone, never contacted).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"CF {lid}", fit_score=random.randint(0, 100))

    # Bucket 9 — rule 9 (never contacted, sendable email only, no phone).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"IE {lid}", fit_score=random.randint(0, 100),
                           phone=None, primary_email=f"p-{lid}@x.test")
        await _seed_contact_email(sm, lid, f"c-{lid}@x.test")

    # Bucket 10 — rule 10 (dormant: has old call outside the windows).
    for _ in range(n_per_bucket):
        lid = nid()
        await _seed_broker(sm, lid, name=f"DM {lid}", fit_score=random.randint(0, 100),
                           phone=None)
        await _seed_call(sm, lid, "not_interested", days_ago=200)

    # Null-fit bucket — 20 brokers covering various rule branches with fit=None.
    for k in range(20):
        lid = nid()
        await _seed_broker(sm, lid, name=f"NF {lid}", fit_score=None)
        if k % 3 == 0:
            await _seed_call(sm, lid, "callback", days_ago=1, callback_at=_TODAY)
        elif k % 3 == 1:
            await _seed_sent(sm, lid, days_ago=3)

    # Duplicate-name bucket — same lower(name), different id → id tiebreak.
    for k in range(10):
        lid = nid()
        await _seed_broker(sm, lid, name="Dup Name", fit_score=50 - k)

    # Suppression hit — rule 9 should NOT fire for a suppressed email.
    for _ in range(5):
        lid = nid()
        await _seed_broker(sm, lid, name=f"SP {lid}", fit_score=random.randint(0, 100),
                           phone=None, primary_email=f"sp-{lid}@x.test")
        em = f"sp-{lid}@x.test"
        await _seed_contact_email(sm, lid, em)
        async with sm() as s:
            s.add(Suppression(email=em, reason="do_not_contact"))
            await s.commit()

    return i


async def test_sql_order_matches_python_ranker(sm):
    """AC2 — SQL ranker emits the same (id, kind, reason) sequence as the
    Python ranker for a rule-coverage fixture of ~500 brokers.
    """
    random.seed(42)
    total = await _seed_rule_coverage_fixture(sm, n_per_bucket=40)
    assert 400 < total < 600, f"fixture size unexpected: {total}"

    py = await py_list_brokers(sm)
    sql = await rank_brokers(sm, limit=1000)

    assert len(sql.rows) == len(py.items), (
        f"SQL returned {len(sql.rows)} rows, Python returned {len(py.items)}"
    )

    py_triples = [(r.id, r.next_action.kind, r.next_action.reason) for r in py.items]
    sql_triples = [(r.id, r.next_action.kind, r.next_action.reason) for r in sql.rows]
    # Compare pair-wise to surface the first drift readably.
    for i, (p, q) in enumerate(zip(py_triples, sql_triples)):
        assert p == q, f"drift at index {i}: python={p} sql={q}"
    assert py_triples == sql_triples


# ---------- keyset -------------------------------------------------------


async def test_page_walk_concat_equals_single_page(sm):
    """AC3 — walking every page at limit=37 reproduces the single
    limit=1000 page exactly; no row dropped, no row duplicated."""
    random.seed(7)
    total = 0
    # Seed 1000 brokers across a mix of rules with a wide fit distribution.
    for k in range(1000):
        lid = f"K{k:04d}"
        fit = None if k % 50 == 0 else (k * 7) % 101  # some null-fit rows
        name = f"Row {chr(65 + (k % 26))}-{k:04d}"
        await _seed_broker(sm, lid, name=name, fit_score=fit,
                           phone=("5551234567" if k % 2 == 0 else None))
        if k % 5 == 0:
            await _seed_sent(sm, lid, days_ago=3)
        if k % 11 == 0:
            await _seed_call(sm, lid, "no_answer", days_ago=2)
        total = k + 1

    one_page = await rank_brokers(sm, limit=total + 10)
    assert len(one_page.rows) == total
    one_shot_ids = [r.id for r in one_page.rows]

    walked: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        pages += 1
        page = await rank_brokers(sm, limit=37, cursor=cursor)
        walked.extend(r.id for r in page.rows)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
        assert pages < 100, "paging loop runaway"

    assert len(walked) == len(set(walked)), "duplicate ids across pages"
    assert walked == one_shot_ids, "page-walk order ≠ single-page order"


# ---------- filters + null-fit --------------------------------------------


async def test_filter_and_null_fit_ordering(sm):
    """AC4 — filter next_action=call + has_phone=true + min_fit=0 yields the
    same set-and-order as the Python path, with null-fit rows strictly after
    real-fit rows inside each priority bucket.
    """
    random.seed(13)
    # 50 call-eligible with phone (rule 7 or 8), varied fit incl. null.
    for k in range(50):
        lid = f"C{k:03d}"
        fit = None if k % 7 == 0 else (k * 11) % 101
        await _seed_broker(sm, lid, name=f"Call {k:03d}", fit_score=fit,
                           phone="5551234567")
        if k % 3 == 0:
            await _seed_call(sm, lid, "no_answer", days_ago=1)
    # 30 email-only (no phone), these must NOT appear.
    for k in range(30):
        lid = f"E{k:03d}"
        await _seed_broker(sm, lid, name=f"Email {k:03d}", fit_score=42,
                           phone=None, primary_email="x@x.test")
        await _seed_contact_email(sm, lid, f"e-{lid}@x.test")

    filters = RankFilters(next_action="call", has_phone=True, min_fit=0)
    sql = await rank_brokers(sm, filters=filters, limit=200)
    py = await py_list_brokers(sm, next_action="call", has_phone=True, min_fit=0)

    sql_ids = [r.id for r in sql.rows]
    py_ids = [r.id for r in py.items]
    assert sql_ids == py_ids, f"\nsql: {sql_ids}\npy:  {py_ids}"
    # All results must start with 'C'.
    assert all(i.startswith("C") for i in sql_ids)

    # Within each next_action-priority bucket (here all 'call'), real-fit
    # rows must come before null-fit rows.
    saw_null = False
    for r in sql.rows:
        if r.fit_score is None:
            saw_null = True
        else:
            assert not saw_null, (
                f"real-fit row {r.id} after a null-fit row — ordering broken"
            )


# ---------- EXPLAIN at 25k is covered by the perf test. --------------------
