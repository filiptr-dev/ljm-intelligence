"""Tenant isolation on real Postgres 16 — the behaviour we're betting on.

Opt-in: `DATABASE_URL_TEST_PG` points at a throwaway PG16 the test may own.
Four tests cover the plan's non-negotiables:

* `test_repository_scope` — a service-shaped query filtered by tenant A
  cannot see tenant B's rows.
* `test_rls_backstop` — even without a WHERE clause, with
  `app.tenant_id = A` set, the DB returns only A's rows.
* `test_cross_tenant_write_blocked` — INSERT of a row whose `tenant_id`
  doesn't match `app.tenant_id` raises an RLS WITH CHECK violation.
* `test_admin_sentinel_sees_all` — `app.tenant_id = ''` returns rows for
  both tenants (the ops/superadmin path).

Postgres bypasses RLS for superusers even with FORCE ROW LEVEL SECURITY,
so these tests connect as a dedicated non-superuser role `app_user`.
Neon production app roles are non-superuser; this test mirrors prod.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL_TEST_PG"),
        reason="set DATABASE_URL_TEST_PG=postgresql+psycopg://... to run",
    ),
    pytest.mark.asyncio,
]


LJM = "01LJMORGLJM00000000000000A"
OTHER = "01OTHERTENANT0000000000000"


def _nonsuperuser_url() -> str:
    """Derive a non-superuser DSN from the superuser DSN.

    We created role `app_user / app` in the per-session fixture below.
    """
    base = os.environ["DATABASE_URL_TEST_PG"]
    # Swap credentials; keep host+db.
    _, after = base.split("://", 1)
    creds, hostpath = after.split("@", 1)
    return f"postgresql+psycopg://app_user:app@{hostpath}"


@pytest_asyncio.fixture
async def pg_setup():
    """Prepare the schema + two tenants + two leads + a non-superuser role."""
    super_url = os.environ["DATABASE_URL_TEST_PG"]
    engine = create_async_engine(super_url, connect_args={"prepare_threshold": None})
    try:
        async with engine.begin() as conn:
            # Non-superuser role so RLS is not bypassed. DROP OWNED first so
            # a prior test run's grants don't block DROP ROLE.
            await conn.execute(
                text(
                    "DO $$ BEGIN "
                    "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user') THEN "
                    "EXECUTE 'DROP OWNED BY app_user CASCADE'; "
                    "EXECUTE 'DROP ROLE app_user'; "
                    "END IF; END $$"
                )
            )
            await conn.execute(text("CREATE ROLE app_user LOGIN PASSWORD 'app'"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO app_user"))
            await conn.execute(text("GRANT ALL ON ALL TABLES IN SCHEMA public TO app_user"))
            await conn.execute(text("GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO app_user"))

            # Ensure both tenants exist.
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, slug, name) "
                    "VALUES (:id, 'other', 'Other Co') "
                    "ON CONFLICT (slug) DO NOTHING"
                ),
                {"id": OTHER},
            )

            # Clean slate for the test leads.
            await conn.execute(
                text("DELETE FROM leads WHERE id IN ('iso_A', 'iso_B')")
            )
            await conn.execute(
                text(
                    "INSERT INTO leads (id, name, kind, state, tenant_id) "
                    "VALUES ('iso_A', 'A Co', 'broker', 'IL', :t)"
                ),
                {"t": LJM},
            )
            await conn.execute(
                text(
                    "INSERT INTO leads (id, name, kind, state, tenant_id) "
                    "VALUES ('iso_B', 'B Co', 'broker', 'IL', :t)"
                ),
                {"t": OTHER},
            )
        yield
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM leads WHERE id IN ('iso_A','iso_B')"))
        await engine.dispose()


@pytest_asyncio.fixture
async def app_session(pg_setup):
    """A session connected as the non-superuser role so RLS applies."""
    engine = create_async_engine(
        _nonsuperuser_url(), connect_args={"prepare_threshold": None}
    )
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with sm() as s:
            yield s
    finally:
        await engine.dispose()


async def _bind(session: AsyncSession, tenant: str) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": tenant},
    )


async def test_rls_backstop(app_session):
    await _bind(app_session, LJM)
    rows = (
        await app_session.execute(text("SELECT id FROM leads WHERE id IN ('iso_A','iso_B')"))
    ).scalars().all()
    assert rows == ["iso_A"], rows

    await _bind(app_session, OTHER)
    rows = (
        await app_session.execute(text("SELECT id FROM leads WHERE id IN ('iso_A','iso_B')"))
    ).scalars().all()
    assert rows == ["iso_B"], rows


async def test_repository_scope(app_session):
    """A WHERE tenant_id = $A query returns only A — same answer as RLS alone."""
    await _bind(app_session, LJM)
    rows = (
        await app_session.execute(
            text(
                "SELECT id FROM leads "
                "WHERE tenant_id = :t AND id IN ('iso_A','iso_B')"
            ),
            {"t": LJM},
        )
    ).scalars().all()
    assert rows == ["iso_A"]


async def test_cross_tenant_write_blocked(app_session):
    """INSERT with mismatched tenant_id must raise an RLS WITH CHECK violation."""
    await _bind(app_session, LJM)
    with pytest.raises(Exception) as exc_info:
        await app_session.execute(
            text(
                "INSERT INTO leads (id, name, kind, state, tenant_id) "
                "VALUES ('iso_X', 'X', 'broker', 'IL', :t)"
            ),
            {"t": OTHER},
        )
    assert "row-level security" in str(exc_info.value).lower()


async def test_admin_sentinel_sees_all(app_session):
    """Admin sentinel (`app.tenant_id = ''`) returns both tenants' rows."""
    await _bind(app_session, "")
    rows = (
        await app_session.execute(
            text("SELECT id FROM leads WHERE id IN ('iso_A','iso_B') ORDER BY id")
        )
    ).scalars().all()
    assert rows == ["iso_A", "iso_B"], rows
