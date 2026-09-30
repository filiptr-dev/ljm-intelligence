"""create_engine() must work with a sqlite URL, not just Postgres/Neon.

Regression: prior to this fix, create_engine() always passed
`connect_args={"prepare_threshold": None, "connect_timeout": 15}` (psycopg-only
kwargs) plus pool sizing. aiosqlite rejects both, so any local sqlite
DATABASE_URL crashed at first connect. The fix branches on
`make_url(url).get_backend_name()` and skips the Postgres knobs for sqlite.

We call the ORIGINAL create_engine (aliased in conftest as
`_orig_create_engine`) — the module-level shim in conftest handles sqlite by
returning a StaticPool engine, which would mask the bug.
"""

import pytest
from sqlalchemy import text

from app import db as _db_mod
from app.config import Settings


@pytest.mark.asyncio
async def test_create_engine_accepts_sqlite_url() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    engine = _db_mod._orig_create_engine(settings)  # type: ignore[attr-defined]
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("select 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


def test_create_engine_postgres_url_still_configures_pool() -> None:
    """Postgres branch keeps its psycopg + pool settings intact (no connect)."""
    settings = Settings(database_url="postgresql+psycopg://u:p@localhost/db")
    engine = _db_mod._orig_create_engine(settings)  # type: ignore[attr-defined]
    # Async engines proxy through sync_engine; pool sizing lives there.
    assert engine.sync_engine.pool.size() == settings.db_pool_size
