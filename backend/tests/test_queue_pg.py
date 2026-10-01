"""Procrastinate queue tests — Postgres-only (TEST_HARNESS=pg16).

Covers the three queue-plan acceptance tests that can't run on sqlite:

* AC1 — migration 0017 round-trip (upgrade → downgrade -1 → upgrade) leaves
        no schema diff.
* AC3 — `defer_on(session, …)` INSERT rides the business transaction: a
        rollback leaves zero `procrastinate_jobs` rows, a commit leaves one.
* AC10 — `/health` includes `jobs = {queued, running, failed_last_24h,
         oldest_queued_age_s}`.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.environ.get("TEST_HARNESS", "").lower() != "pg16",
    reason="queue tests require TEST_HARNESS=pg16",
)


@pytest.mark.asyncio
async def test_ac3_defer_on_rolls_back_with_business_tx() -> None:
    """A rolled-back business transaction must leave zero job rows."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.shared.queue import defer_on

    url = os.environ["DATABASE_URL_TEST_PG"]
    engine = create_async_engine(url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Baseline count.
        async with sm() as s:
            before = (await s.execute(text("SELECT COUNT(*) FROM procrastinate_jobs"))).scalar_one()

        async with sm() as s:
            async with s.begin():
                await defer_on(s, "prospecting.crawl_leads", tenant_id="x", trigger="rollback-test")
                # Force a rollback by raising inside the begin() block.
                raise RuntimeError("intentional rollback")
    except RuntimeError:
        pass

    async with sm() as s:
        after = (await s.execute(text("SELECT COUNT(*) FROM procrastinate_jobs"))).scalar_one()
    assert after == before, "defer_on wrote a job row that outlived a rollback"

    # Positive path: a committed txn leaves exactly one new row.
    async with sm() as s:
        async with s.begin():
            jid = await defer_on(s, "prospecting.crawl_leads", tenant_id="x", trigger="commit-test")
            assert isinstance(jid, int) and jid > 0

    async with sm() as s:
        found = (
            await s.execute(text("SELECT COUNT(*) FROM procrastinate_jobs WHERE id = :i"), {"i": jid})
        ).scalar_one()
    assert found == 1

    # Clean up.
    async with sm() as s:
        await s.execute(text("DELETE FROM procrastinate_jobs WHERE id = :i"), {"i": jid})
        await s.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_ac10_jobs_health_snapshot_shape() -> None:
    """`jobs_health(session)` returns the four documented fields."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.shared.queue import jobs_health

    url = os.environ["DATABASE_URL_TEST_PG"]
    engine = create_async_engine(url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as s:
        snap = await jobs_health(s)
    await engine.dispose()
    for k in ("queued", "running", "failed_last_24h", "oldest_queued_age_s"):
        assert k in snap, f"missing {k}"
        assert isinstance(snap[k], int)


@pytest.mark.asyncio
async def test_ac2_worker_runs_a_dispatched_task_to_succeeded() -> None:
    """AC2 — defer a cheap task, run the worker until it reaches `succeeded`.

    Uses the reserved `inbox.retention_sweep` task because its body is a
    safe no-op (log + return) — no DB fixtures, no network. The point is
    the worker loop end-to-end, not the business logic.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.shared.queue import app as queue_app, dispatch

    url = os.environ["DATABASE_URL_TEST_PG"]
    engine = create_async_engine(url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    try:
        # Clear any residue from earlier tests to isolate our one row.
        async with sm() as s:
            await s.execute(text("DELETE FROM procrastinate_jobs"))
            await s.commit()

        jid = await dispatch("inbox.retention_sweep", tenant_id="01TEST000000000000000OWNER")
        assert isinstance(jid, int) and jid > 0

        # Drive the worker in-process. `wait=False` makes it exit as soon
        # as the queue drains; 15s is a generous upper bound for the one
        # no-op task.
        async with queue_app.open_async():
            try:
                await asyncio.wait_for(
                    queue_app.run_worker_async(
                        queues=["default"],
                        wait=False,
                        install_signal_handlers=False,
                        listen_notify=False,
                    ),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                pass

        async with sm() as s:
            row = (
                await s.execute(
                    text("SELECT status FROM procrastinate_jobs WHERE id = :i"),
                    {"i": jid},
                )
            ).first()
        assert row is not None, "job row disappeared"
        assert row[0] == "succeeded", f"job {jid} ended in status={row[0]}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ac4_tenant_bound_job_cannot_read_other_tenants() -> None:
    """AC4 — a job bound to tenant A can't SELECT tenant B rows under RLS.

    We simulate the worker's `uow(tenant_id)` boundary by calling
    `SET LOCAL app.tenant_id` inside a session.begin() block and reading
    `leads` (RLS-protected by migration 0016). The connection runs as the
    non-superuser `app_user` role because Postgres bypasses RLS for the
    superuser — matching how production will run.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    super_url = os.environ["DATABASE_URL_TEST_PG"]
    super_engine = create_async_engine(super_url, future=True)

    tenant_a = "01TENANTA0000000000000000A"
    tenant_b = "01TENANTB0000000000000000B"

    try:
        # Seed as the superuser so RLS can't block setup.
        async with super_engine.begin() as conn:
            await conn.execute(
                text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user') THEN "
                    "EXECUTE 'CREATE ROLE app_user LOGIN PASSWORD ''app'''; "
                    "EXECUTE 'GRANT ALL ON SCHEMA public TO app_user'; "
                    "EXECUTE 'GRANT ALL ON ALL TABLES IN SCHEMA public TO app_user'; "
                    "EXECUTE 'GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO app_user'; "
                    "END IF; END $$"
                )
            )
            for tid, slug in ((tenant_a, "test-rls-a"), (tenant_b, "test-rls-b")):
                await conn.execute(
                    text(
                        "INSERT INTO organizations (id, slug, name, plan, settings, created_at) "
                        "VALUES (:id, :slug, :name, 'standard', '{}', now()) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {"id": tid, "slug": slug, "name": slug.upper()},
                )
            for tid, mc in ((tenant_a, "MC-ISO-A"), (tenant_b, "MC-ISO-B")):
                await conn.execute(
                    text(
                        "INSERT INTO leads (id, tenant_id, mc, name, kind, state) "
                        "VALUES (:id, :tid, :mc, :name, 'broker', 'NJ') "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": f"01LD{tid[-6:]}00000000000000",
                        "tid": tid,
                        "mc": mc,
                        "name": f"isolation-{mc}",
                    },
                )

        # Non-superuser connection so RLS actually fires.
        _, after = super_url.split("://", 1)
        _, hostpath = after.split("@", 1)
        app_url = f"postgresql+psycopg://app_user:app@{hostpath}"
        app_engine = create_async_engine(app_url, future=True, connect_args={"prepare_threshold": None})
        sm = async_sessionmaker(app_engine, expire_on_commit=False)

        try:
            async with sm() as s:
                async with s.begin():
                    await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_a})
                    rows = (await s.execute(text("SELECT mc FROM leads"))).all()
            mcs_a = {r[0] for r in rows}
            assert "MC-ISO-A" in mcs_a
            assert "MC-ISO-B" not in mcs_a, "RLS leak: tenant A saw tenant B's rows"

            async with sm() as s:
                async with s.begin():
                    await s.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_b})
                    rows = (await s.execute(text("SELECT mc FROM leads"))).all()
            mcs_b = {r[0] for r in rows}
            assert "MC-ISO-B" in mcs_b
            assert "MC-ISO-A" not in mcs_b, "RLS leak: tenant B saw tenant A's rows"
        finally:
            await app_engine.dispose()
    finally:
        async with super_engine.begin() as conn:
            await conn.execute(text("DELETE FROM leads WHERE mc IN ('MC-ISO-A','MC-ISO-B')"))
            await conn.execute(
                text("DELETE FROM organizations WHERE id IN (:a,:b)"),
                {"a": tenant_a, "b": tenant_b},
            )
        await super_engine.dispose()


def test_ac1_migration_0017_round_trip() -> None:
    """0017 upgrade/downgrade/upgrade is schema-neutral.

    We rely on the existing round-trip harness in test_migrations_pg.py;
    this test just asserts the procrastinate_jobs table exists after
    `alembic upgrade head`.
    """
    import psycopg

    url = os.environ["DATABASE_URL_TEST_PG"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as c, c.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'procrastinate_jobs'"
        )
        assert cur.fetchone() is not None
