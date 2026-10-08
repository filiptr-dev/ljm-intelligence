"""Real PG16 end-to-end boot + tenancy proof.

Opt-in: `DATABASE_URL_TEST_PG` must be set to a throwaway Postgres 16.
Verifies the review's BLOCKER fix:

* `alembic upgrade head` succeeds on a fresh DB (no sqlite in sight).
* The seeded demo owner can log in through the real route.
* A Lead inserted via the ORM (no explicit `tenant_id`) is stamped with
  LJM's tenant_id by the `TenantMixin` + `before_insert` listener.
* The `/brokers`, `/overview`, `/loads` list endpoints return 200 for the
  seeded owner — proves routing + session wiring boot cleanly against PG.
"""

from __future__ import annotations

import importlib
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL_TEST_PG"),
        reason="set DATABASE_URL_TEST_PG=postgresql://... to run",
    ),
    pytest.mark.asyncio,
]


@pytest.fixture
async def pg_app(request):
    """Build a FastAPI app bound to PG16 and manually populate app.state.

    The conftest module monkey-patches `app.main.create_app` and
    `app.db.create_engine` to always use sqlite. We reach into the originals
    and populate `app.state.sessionmaker` outside the lifespan so the
    ASGITransport (which doesn't run lifespan) still works.
    """
    os.environ["DATABASE_URL"] = os.environ["DATABASE_URL_TEST_PG"]

    # In TEST_HARNESS=pg16 mode, conftest's autouse TRUNCATE clears users +
    # settings between tests. This fixture restores the migration seeds so
    # the demo-owner login below finds the row and the JWT secret the token
    # path reads from `settings.auth_jwt_secret`.
    try:
        request.getfixturevalue("_pg_restore_seeds")
    except Exception:
        pass

    import app.db as db_mod
    import app.main as main_mod
    from app.config import Settings
    from app.db import create_sessionmaker

    s = Settings()
    assert "postgresql" in s.database_url, s.database_url

    orig_create_app = getattr(main_mod, "_orig_create_app", main_mod.create_app)
    orig_create_engine = getattr(db_mod, "_orig_create_engine", db_mod.create_engine)

    app = orig_create_app(s)
    engine = orig_create_engine(s)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    app.state.settings = s

    # Clear the auth bypass if conftest installed one.
    from app.auth.deps import current_user, require_user_or_cron

    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(require_user_or_cron, None)

    try:
        yield app
    finally:
        await engine.dispose()


async def test_login_and_list_endpoints(pg_app):
    """Boot the app on PG16, log in, hit three read endpoints."""
    transport = ASGITransport(app=pg_app)
    async with AsyncClient(transport=transport, base_url="http://pg-test") as c:
        # health first
        r = await c.get("/health")
        assert r.status_code == 200, r.text

        # login as demo owner (seeded by migration 0008)
        r = await c.post(
            "/auth/login",
            json={"email": "owner@ljm.com", "password": "password"},
        )
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Three real read endpoints — proves routers + DB wiring work on PG.
        for path in ("/brokers", "/overview/today", "/loads"):
            r = await c.get(path, headers=headers)
            assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"


async def test_orm_insert_stamps_tenant_id(pg_app):
    """A Lead inserted without an explicit tenant_id gets LJM's id via TenantMixin."""
    from app.models import Lead
    from app.shared.tenant import TenantId, set_tenant
    from app.shared.orm import LJM_TENANT_ID

    sessionmaker = pg_app.state.sessionmaker

    # Simulate a request context: tenant bound to LJM.
    set_tenant(TenantId(LJM_TENANT_ID))

    async with sessionmaker() as session:
        async with session.begin():
            # SET LOCAL via bound param — same path as the real request flow.
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": LJM_TENANT_ID},
            )
            # Insert without specifying tenant_id — the mixin's before_insert
            # listener must stamp it.
            lead = Lead(
                id="test_lead_tenant_stamp",
                name="Tenancy Test Co",
                kind="broker",
                state="IL",
            )
            session.add(lead)
            await session.flush()

            # Read it back and assert tenant_id matches LJM.
            row = await session.execute(
                text("SELECT tenant_id FROM leads WHERE id = :id"),
                {"id": "test_lead_tenant_stamp"},
            )
            got = row.scalar_one()
            assert got == LJM_TENANT_ID, f"expected LJM, got {got!r}"

            # Clean up.
            await session.execute(
                text("DELETE FROM leads WHERE id = :id"),
                {"id": "test_lead_tenant_stamp"},
            )


async def test_raw_insert_hits_server_default_safety_net(pg_app):
    """A raw SQL insert that omits tenant_id falls through to server_default = LJM."""
    from app.shared.orm import LJM_TENANT_ID

    sessionmaker = pg_app.state.sessionmaker
    async with sessionmaker() as session:
        async with session.begin():
            # No set_config → no context — proves the DB-level safety net.
            await session.execute(
                text(
                    "INSERT INTO leads (id, name, kind, state) "
                    "VALUES ('raw_safety_net', 'Raw Co', 'broker', 'IL')"
                )
            )
            row = await session.execute(
                text("SELECT tenant_id FROM leads WHERE id = 'raw_safety_net'")
            )
            assert row.scalar_one() == LJM_TENANT_ID
            await session.execute(text("DELETE FROM leads WHERE id = 'raw_safety_net'"))
