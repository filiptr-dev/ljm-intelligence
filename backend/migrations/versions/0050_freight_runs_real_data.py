"""freight_runs → real data: nullable costs/revenue + stable upsert key.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-13

Three moves, in order:

* Make the numeric fields that aren't always known ``NULL`` -able:
  ``miles``, ``revenue_usd``, ``cost_fuel_usd``, ``cost_driver_usd``,
  ``cost_load_usd``, ``cost_dispatch_usd``. The lanes page already
  COALESCEs these to 0 in aggregates — the schema change just lets us
  stop fabricating a 0 at ingest when the truth is "we don't know".

* Add ``source_ref`` + a unique ``(tenant_id, source, source_ref)``
  index. That's the dedupe anchor the LoadStop / inbox ingest upserts
  against. Nullable + partial unique (``source_ref IS NOT NULL``) so
  the historical ``source='demo'`` rows (none remain after step 3)
  wouldn't have collided.

* Delete every ``source='demo'`` row for the LJM tenant. Migrations
  0036 / 0037 / 0039 seeded ~2000 of these on every fresh install; the
  live code path never planted them, but they were still arriving
  through alembic. Dropping them here (and leaving the three seed
  migrations as history) means a fresh install still runs those
  statements, this migration then wipes the result, and no code path
  re-creates them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_T = "freight_runs"
_NULLABLE_COLS = (
    ("miles", sa.Integer()),
    ("revenue_usd", sa.Numeric(10, 2)),
    ("cost_fuel_usd", sa.Numeric(10, 2)),
    ("cost_driver_usd", sa.Numeric(10, 2)),
    ("cost_load_usd", sa.Numeric(10, 2)),
    ("cost_dispatch_usd", sa.Numeric(10, 2)),
)


def upgrade() -> None:
    for name, type_ in _NULLABLE_COLS:
        op.alter_column(_T, name, existing_type=type_, nullable=True)

    op.add_column(_T, sa.Column("source_ref", sa.String(length=128), nullable=True))
    # Partial unique: NULL source_ref (legacy rows) is permitted; new
    # ingest rows always carry one so the upsert key is honored.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_index(
            "uq_freight_runs_tenant_source_ref",
            _T,
            ["tenant_id", "source", "source_ref"],
            unique=True,
            postgresql_where=sa.text("source_ref IS NOT NULL"),
        )
    else:
        # SQLite in tests: a plain unique on the triple is fine — the
        # test fixtures never insert NULL source_ref rows.
        op.create_index(
            "uq_freight_runs_tenant_source_ref",
            _T,
            ["tenant_id", "source", "source_ref"],
            unique=True,
        )

    # One-shot wipe of dummy history. Scoped to the LJM tenant to match
    # how the seeders scoped their writes.
    op.execute(
        "DELETE FROM freight_runs WHERE source = 'demo'"
    )


def downgrade() -> None:
    op.drop_index("uq_freight_runs_tenant_source_ref", table_name=_T)
    op.drop_column(_T, "source_ref")
    for name, type_ in _NULLABLE_COLS:
        op.alter_column(_T, name, existing_type=type_, nullable=False)
