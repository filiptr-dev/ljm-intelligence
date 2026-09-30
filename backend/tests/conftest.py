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
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# --- 1. Pin DATABASE_URL to an in-memory SQLite BEFORE .env / app import. ---
# pydantic-settings resolves in the order: init kwargs > os.environ > .env file,
# so a value present in os.environ shadows whatever backend/.env holds.
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


_db_mod.create_engine = _test_create_engine
# Keep a handle to the un-shimmed builder so tests can exercise the real
# create_engine() (e.g. verifying the sqlite backend branch) without loading
# a fresh module and re-triggering side effects.
_db_mod._orig_create_engine = _orig_create_engine
# app.main did `from app.db import create_engine` — rebind that name too so the
# lifespan uses our shim.
import app.main as _main_mod

_main_mod.create_engine = _test_create_engine
