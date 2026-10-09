"""Migration round-trip test on real Postgres 16.

Opt-in: skipped unless `DATABASE_URL_TEST_PG` is set (e.g. a throwaway container).
Proves that `base → head → base → head` lands in the same schema — this is the
test that would have caught the 0013 boolean-default class of bug on sqlite.

The round-trip also now reflects the schema at each `head` state and asserts
the table + column signatures match, so a destructive downgrade (dropping a
column that upgrade can't re-create cleanly) fails this test instead of
silently drifting from the declared models.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="set DATABASE_URL_TEST_PG=postgresql://... to run the PG migration round-trip",
)


def _alembic_cfg(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


def _schema_signature(sync_url: str) -> dict[str, list[tuple[str, str, bool]]]:
    """Return {table_name: sorted [(col_name, data_type, is_nullable), ...]}.

    Reflects the live public schema via information_schema — stable enough
    for a round-trip equality check without needing SQLAlchemy's full
    reflect pass. `alembic_version` is excluded because its single row
    naturally differs between the two upgrade passes.
    """
    sig: dict[str, list[tuple[str, str, bool]]] = {}
    with psycopg.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name <> 'alembic_version'
            ORDER BY table_name, column_name
            """
        )
        for table, col, dtype, nullable in cur.fetchall():
            sig.setdefault(table, []).append((col, dtype, nullable == "YES"))
    return sig


def test_round_trip_schema_stable():
    url = os.environ["DATABASE_URL_TEST_PG"]
    sync_url = url.replace("postgresql+psycopg://", "postgresql://")
    cfg = _alembic_cfg(url)

    # Clean slate.
    command.downgrade(cfg, "base")

    # First upgrade — snapshot the resulting schema.
    command.upgrade(cfg, "head")
    first = _schema_signature(sync_url)
    assert first, "no public tables after first upgrade — alembic didn't run"

    # Round trip.
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    second = _schema_signature(sync_url)

    # Table sets equal.
    assert set(first) == set(second), {
        "only_in_first": sorted(set(first) - set(second)),
        "only_in_second": sorted(set(second) - set(first)),
    }
    # Each table's column signature is byte-equal.
    for table in sorted(first):
        assert first[table] == second[table], f"column drift in {table!r}"
