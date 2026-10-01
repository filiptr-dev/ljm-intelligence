"""Test config — fail fast if a test would touch a non-local database.

Why this file matters
---------------------
Slice 2b flipped ``osm_overpass_enabled=True`` by default and the crawl route
runs its pipeline in a FastAPI ``BackgroundTasks`` — so a test that forgot to
override the sessionmaker or the Overpass fetcher would happily write to the
DATABASE_URL loaded from ``backend/.env`` (production Neon) and make live HTTP
calls to Overpass/FMCSA/Gemini.

Two belts and a pair of suspenders, in order:

1. BEFORE any app import, hard-set ``DATABASE_URL`` in ``os.environ`` to an
   in-memory SQLite URL. ``pydantic-settings`` reads real env vars ahead of
   ``.env``, so this wins even though ``backend/.env`` exists on disk.
2. Load ``backend/.env`` with ``override=False`` — we still want the other
   knobs (log level, region config), just never the prod DB URL.
3. Force ``APP_ENV=test`` so any production-only branch is short-circuited.
4. Import ``Settings`` and assert its effective ``database_url`` is a
   local/sqlite URL. If somebody points tests at a remote host in the future,
   the whole test session aborts at collection time rather than corrupting
   prod on the first background task.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# --- 0. PG16 harness opt-in. Set TEST_HARNESS=pg16 (or DATABASE_URL_TEST_PG) --
# to run the full suite against a session-scoped throwaway Postgres 16
# container instead of in-memory sqlite. This is the architecture-foundation
# target harness; the sqlite path stays as the easy local default.
_PG_HARNESS = (
    os.environ.get("TEST_HARNESS", "").lower() == "pg16"
    or bool(os.environ.get("DATABASE_URL_TEST_PG"))
)

_PG_TEST_URL: str | None = None  # set after we spin up / connect to the container


def _ensure_pg16_container() -> str:
    """Return a `postgresql+psycopg://` URL pointing at a running PG16.

    Reuses an already-running ``ljm-test-pg16`` container on port 5444 if
    present (so repeated local runs are instant); otherwise starts one with
    ``docker run`` and polls until ready. CI can pre-seed the URL via
    ``DATABASE_URL_TEST_PG`` and skip the docker dance entirely.
    """
    env_url = os.environ.get("DATABASE_URL_TEST_PG")
    if env_url:
        return env_url
    if shutil.which("docker") is None:
        raise RuntimeError(
            "TEST_HARNESS=pg16 requires docker on PATH or DATABASE_URL_TEST_PG set"
        )
    name = "ljm-test-pg16"
    port = int(os.environ.get("LJM_TEST_PG_PORT", "5444"))
    # Reuse if already running.
    probe = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        check=False, capture_output=True, text=True,
    )
    if probe.stdout.strip() != name:
        subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", name,
                "-e", "POSTGRES_PASSWORD=pg", "-e", "POSTGRES_USER=pg",
                "-e", "POSTGRES_DB=ljm_test",
                "-p", f"{port}:5432", "postgres:16",
            ],
            check=True, capture_output=True,
        )
        # Poll pg_isready for up to 20s.
        for _ in range(40):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "pg", "-d", "ljm_test"],
                check=False, capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"PG16 container {name} failed to become ready")
    return f"postgresql+psycopg://pg:pg@localhost:{port}/ljm_test"


def _reset_pg16_schema(url: str) -> None:
    """Drop everything in `public` and the `app_user` role, then run alembic
    upgrade head once. Session-scoped — called before any test engine spins up.
    """
    import psycopg

    sync_url = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(sync_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # test_tenancy_isolation leaves app_user behind; drop OWNED first
            # so DROP ROLE doesn't fail on dependent privileges.
            cur.execute(
                """
                DO $$
                BEGIN
                  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user') THEN
                    EXECUTE 'DROP OWNED BY app_user CASCADE';
                    EXECUTE 'DROP ROLE app_user';
                  END IF;
                END $$;
                """
            )
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO pg")

    # Alembic upgrade head against the test URL.
    backend_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["DATABASE_URL_DIRECT"] = url
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(backend_root), env=env, check=True, capture_output=True,
    )


# --- 1. Pin DATABASE_URL BEFORE .env / app import. ---
# pydantic-settings resolves init kwargs > os.environ > .env file, so a value
# in os.environ wins over whatever backend/.env holds. In sqlite mode we pin
# to an in-memory URL; in PG16 mode we pin to the throwaway container's URL.
if _PG_HARNESS:
    _PG_TEST_URL = _ensure_pg16_container()
    _reset_pg16_schema(_PG_TEST_URL)
    _SAFE_DB_URL = _PG_TEST_URL
    # Expose for opt-in tests that read this var directly.
    os.environ.setdefault("DATABASE_URL_TEST_PG", _PG_TEST_URL)
else:
    _SAFE_DB_URL = "sqlite+aiosqlite:///:memory:"
os.environ["DATABASE_URL"] = _SAFE_DB_URL

# --- 2. Load .env AFTER, with override=False, so it can't clobber us. ---
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

# --- 3. Force test app_env. ---
os.environ["APP_ENV"] = "test"

# --- 4. Assert the effective Settings.database_url is safe. ---
# Imported here (after the env pin) so Settings picks up our sqlite URL.
from app.config import Settings

_settings = Settings()
_db_url = _settings.database_url

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def _is_safe_db_url(url: str) -> bool:
    if url.startswith("sqlite"):
        return True
    parsed = urlparse(url)
    return (parsed.hostname or "") in _LOCAL_HOSTS


assert _is_safe_db_url(_db_url), (
    f"Refusing to run tests against a non-local database: {_db_url!r}. "
    "Check that backend/tests/conftest.py pinned DATABASE_URL before any app import, "
    "and that no other conftest / plugin re-imported Settings first."
)

# --- 5. Reroute create_engine for sqlite URLs. ---
# The production create_engine() passes psycopg / pool_size / connect_timeout kwargs
# that SQLite's aiosqlite driver rejects. Tests that open the lifespan
# (e.g. test_email_draft, test_health) need a working engine — so we swap in a
# sqlite-friendly builder at import time, but ONLY when the URL is sqlite,
# leaving the real code path untouched for anything else.
from sqlalchemy.ext.asyncio import create_async_engine as _real_create_async_engine

from app import db as _db_mod

_orig_create_engine = _db_mod.create_engine


def _test_create_engine(settings):
    url = settings.database_url
    if url.startswith("sqlite"):
        # StaticPool keeps the single in-memory DB shared across sessions in
        # the same process, so schema created via Base.metadata.create_all is
        # visible to subsequent AsyncSessions.
        from sqlalchemy.pool import StaticPool

        return _real_create_async_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return _orig_create_engine(settings)


# --- 5b. In PG16 mode, redirect test-local sqlite engines to the PG test DB. -
# Existing per-test fixtures do `create_async_engine("sqlite+aiosqlite:///:memory:")`
# directly. We shim the SQLAlchemy entry point so the sqlite URL silently
# becomes the shared PG test DB; `Base.metadata.create_all` is a no-op because
# alembic upgrade head already ran at session start.
if _PG_HARNESS:
    import sqlalchemy.ext.asyncio as _sa_async

    _orig_create_async_engine = _sa_async.create_async_engine

    def _pg_redirect_create_async_engine(url: str | object, **kwargs):
        if isinstance(url, str) and url.startswith("sqlite"):
            # Strip sqlite-only kwargs the caller may have passed.
            kwargs.pop("connect_args", None)
            kwargs.pop("poolclass", None)
            kwargs.pop("future", None)
            engine = _orig_create_async_engine(_PG_TEST_URL, **kwargs)
            # FK enforcement is ON. The sqlite harness is permissive by
            # driver default; the PG16 harness is strict by design — tests
            # that insert a referencing row must seed the referenced row
            # first. See projects/ljm-intelligence/tasks/.../
            # 2026-10-01-pg-test-harness-real-fks.md.
            return engine
        return _orig_create_async_engine(url, **kwargs)

    _sa_async.create_async_engine = _pg_redirect_create_async_engine
    # Also rebind the symbol tests may have imported.
    import sqlalchemy.ext.asyncio
    sqlalchemy.ext.asyncio.create_async_engine = _pg_redirect_create_async_engine

    # Shim Base.metadata.create_all so tests that call it against the PG DB
    # (where tables already exist from alembic) don't try to re-DDL.
    from app.db import Base as _Base

    _orig_metadata_create_all = _Base.metadata.create_all

    def _noop_create_all(bind=None, *args, **kwargs):
        return None

    _Base.metadata.create_all = _noop_create_all  # type: ignore[method-assign]

    # Snapshot migration-seeded users + settings so tests that depend on them
    # (e.g. test_pg16_boot_and_tenancy's `/auth/login` flow) can restore them
    # via the `_pg_restore_seeds` fixture after autouse TRUNCATE.
    import psycopg as _psycopg

    _snap_url = _PG_TEST_URL.replace("postgresql+psycopg://", "postgresql://")
    _SEED_USERS: list[dict] = []
    _SEED_SETTINGS: list[dict] = []
    with _psycopg.connect(_snap_url) as _c, _c.cursor() as _cur:
        _cur.execute(
            "SELECT id, tenant_id, email, name, role, password_hash, is_active, "
            "created_at, last_login_at FROM users"
        )
        for row in _cur.fetchall():
            _SEED_USERS.append(dict(zip(
                ("id","tenant_id","email","name","role","password_hash",
                 "is_active","created_at","last_login_at"), row, strict=True
            )))
        _cur.execute(
            "SELECT id, auth_jwt_secret, unsubscribe_secret FROM settings"
        )
        for row in _cur.fetchall():
            _SEED_SETTINGS.append(dict(zip(
                ("id","auth_jwt_secret","unsubscribe_secret"),
                row, strict=True,
            )))


_db_mod.create_engine = _test_create_engine
# Keep a handle to the un-shimmed builder so tests can exercise the real
# create_engine() (e.g. verifying the sqlite backend branch) without loading
# a fresh module and re-triggering side effects.
_db_mod._orig_create_engine = _orig_create_engine
# app.main did `from app.db import create_engine` — rebind that name too so the
# lifespan uses our shim.
import app.main as _main_mod

_main_mod.create_engine = _test_create_engine


# --- 6. Install an auth override on every create_app() the tests build. -----
# The feature/simple-password-login build added bearer-JWT auth to every
# non-cron, non-health, non-unsubscribe route. Existing tests build their own
# ``AsyncClient(app=create_app())`` without a token and we deliberately do NOT
# weaken the real auth deps — instead, we wrap ``create_app`` to install a
# ``dependency_overrides`` entry that resolves ``current_user`` /
# ``require_user_or_cron`` to a fixed test principal. Auth-specific tests
# (``test_auth.py``) clear this override on the app they own so the real deps
# run end-to-end.
_orig_create_app = _main_mod.create_app


def _install_auth_bypass(app):
    from app.auth.deps import UserPrincipal, current_user, require_user_or_cron
    from app.identity.dependencies import current_tenant
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.tenant import TenantId, set_tenant

    principal = UserPrincipal(id="01TEST000000000000000OWNER", email="test@ljm-demo.local", role="owner")
    app.dependency_overrides[current_user] = lambda: principal
    app.dependency_overrides[require_user_or_cron] = lambda: principal

    def _test_current_tenant():
        tenant = TenantId(LJM_TENANT_ID)
        set_tenant(tenant)
        return tenant

    app.dependency_overrides[current_tenant] = _test_current_tenant
    return app


def _test_create_app(settings=None):
    app = _orig_create_app(settings) if settings is not None else _orig_create_app()
    return _install_auth_bypass(app)


_main_mod.create_app = _test_create_app


import pytest


if _PG_HARNESS:
    @pytest.fixture
    def _pg_restore_seeds():
        """Restore the migration-seeded users + settings rows. Tests that
        depend on `owner@ljm-demo.local` or the migration JWT secret request
        this fixture; it runs BEFORE the test body so the seeds exist when
        the test touches the DB."""
        import psycopg

        sync_url = _PG_TEST_URL.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(sync_url, autocommit=True) as conn, conn.cursor() as cur:
            for u in _SEED_USERS:
                cur.execute(
                    """
                    INSERT INTO users (id, tenant_id, email, name, role,
                                       password_hash, is_active, created_at, last_login_at)
                    VALUES (%(id)s, %(tenant_id)s, %(email)s, %(name)s, %(role)s,
                            %(password_hash)s, %(is_active)s, %(created_at)s, %(last_login_at)s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    u,
                )
            for s in _SEED_SETTINGS:
                cur.execute(
                    """
                    INSERT INTO settings (id, auth_jwt_secret, unsubscribe_secret)
                    VALUES (%(id)s, %(auth_jwt_secret)s, %(unsubscribe_secret)s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    s,
                )
        yield


    @pytest.fixture(autouse=True)
    def _pg_truncate_between_tests():
        """Clear all data between tests so the shared PG test DB behaves like
        the sqlite in-memory DB (fresh per test). We TRUNCATE every user table
        then re-seed the LJM tenant row the migration planted."""
        yield
        import psycopg

        sync_url = _PG_TEST_URL.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(sync_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                # NOTE: don't touch the app_user role here — tenancy-isolation
                # tests create and reuse it across their own test cases.
                # The session-start _reset_pg16_schema already dropped it once
                # so stale privileges don't survive a prior test session.
                # Grab every user table in public (skip alembic_version so
                # upgrade state survives), truncate them all in one shot.
                # Skip only the LJM organization row + alembic_version.
                # Everything else — including users and settings — is cleared
                # between tests; tests that need migration-seeded users/settings
                # must re-seed inside their own fixture (we provide the
                # `_pg_restore_seeds` fixture below for that).
                cur.execute(
                    """
                    SELECT tablename FROM pg_tables
                    WHERE schemaname='public'
                      AND tablename NOT IN ('alembic_version', 'organizations')
                    """
                )
                tables = [row[0] for row in cur.fetchall()]
                if tables:
                    joined = ", ".join(f'"{t}"' for t in tables)
                    cur.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
                # Re-seed the LJM tenant row + demo owner identity so
                # TenantMixin inserts have a valid FK target.
                from app.shared.orm import LJM_TENANT_ID
                cur.execute(
                    """
                    INSERT INTO organizations (id, slug, name, plan, settings, created_at)
                    VALUES (%s, 'ljm', 'LJM International', 'standard', '{}', now())
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (LJM_TENANT_ID,),
                )


@pytest.fixture
def auth_bypass():
    """Expose the installer so a test that builds its own app can opt in."""
    return _install_auth_bypass


@pytest.fixture
def disable_auth_bypass():
    """Context-ish helper: tests that need the REAL auth deps call this on their app."""

    def _clear(app):
        from app.auth.deps import current_user, require_user_or_cron

        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(require_user_or_cron, None)
        return app

    return _clear
