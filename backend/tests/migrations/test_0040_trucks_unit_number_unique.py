"""Migration 0040: unique (tenant_id, unit_number) on trucks — Postgres up/down round-trip (opt-in)."""

from __future__ import annotations

import os

import pytest

from tests.analysis.lanes_fixtures import TENANT

pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="set DATABASE_URL_TEST_PG=postgresql+psycopg://... for the PG round-trip",
)


@pg
def test_pg_unit_number_is_unique_per_tenant_and_downgrade_drops_it():
    import psycopg
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL_TEST_PG"]
    sync = url.replace("postgresql+psycopg://", "postgresql://")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False

    def insert(tenant: str, unit: str) -> None:
        with psycopg.connect(sync, autocommit=True) as c, c.cursor() as cur:
            cur.execute("INSERT INTO trucks (tenant_id, unit_number, source) VALUES (%s, %s, 'manual')", (tenant, unit))

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")  # the demo seed has 28 distinct numbers, so 0040 applies over real data
    insert(TENANT, "ZZ-1")
    insert("01OTHERTENANT0000000000AA", "ZZ-1")  # same number, other tenant: fine
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(TENANT, "ZZ-1")

    command.downgrade(cfg, "0039")
    insert(TENANT, "ZZ-1")  # constraint gone
    with psycopg.connect(sync, autocommit=True) as c, c.cursor() as cur:
        cur.execute("DELETE FROM trucks WHERE unit_number = 'ZZ-1' AND source = 'manual'")
    command.upgrade(cfg, "head")
