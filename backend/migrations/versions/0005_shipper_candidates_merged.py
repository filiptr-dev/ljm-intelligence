"""shipper_candidates → merged-row model (Slice 2a)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30

Reshape `shipper_candidates` to **one row per company** rather than one row per
(company × source). See the plan's "Amendment (2026-09-30): merged rows".

Why safe now: `shipper_candidates` is currently empty on Neon (0 rows — no
ingest has run yet). No data-preservation logic is required; upgrade and
downgrade both operate on an empty table.

Changes vs 0004:
  * `id` narrows from String(96) → String(32) — the neutral ULID no longer
    encodes a source prefix.
  * `source` column and its CHECK constraint go away, replaced by:
      - `sources JSONB NOT NULL default '[]'` — list of contributing sources.
      - `fmcsa_dot`, `fmcsa_mc`, `osm_ref` — each nullable, each with a
        partial-UNIQUE index WHERE NOT NULL. These are the source-key columns
        the merge fn uses for exact-hit dedupe.
  * `match_reason String(255) NULL` — short human-readable "why merged".
  * The `shipper_candidates_source` index goes away (the column it indexed is
    gone). The `state` and `unpromoted` indexes stay untouched.

Downgrade restores the exact 0004 shape: drop the new columns/indexes, restore
`source` + CHECK + its index, restore `id`'s width. Table is empty during
rollout so no back-fill is possible or required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Drop the source-scoped bits first — the CHECK constraint depends on the
    #    column, and the index depends on it too. Order matters.
    op.drop_index("shipper_candidates_source", table_name="shipper_candidates")
    op.drop_constraint("shipper_candidates_source_check", "shipper_candidates", type_="check")
    op.drop_column("shipper_candidates", "source")

    # 2) Narrow `id` from 96 → 32. Table is empty; alter is a no-op data-wise.
    op.alter_column(
        "shipper_candidates",
        "id",
        existing_type=sa.String(96),
        type_=sa.String(32),
        existing_nullable=False,
    )

    # 3) New source-key columns.
    op.add_column("shipper_candidates", sa.Column("fmcsa_dot", sa.String(32), nullable=True))
    op.add_column("shipper_candidates", sa.Column("fmcsa_mc", sa.String(32), nullable=True))
    op.add_column("shipper_candidates", sa.Column("osm_ref", sa.String(64), nullable=True))

    # 4) `sources` — JSONB list. Keep it JSONB for Postgres, plain JSON on SQLite;
    #    server_default '[]' so existing/new inserts without an explicit value are safe.
    sources_type = sa.JSON().with_variant(
        sa.dialects.postgresql.JSONB(),
        "postgresql",
    )
    op.add_column(
        "shipper_candidates",
        sa.Column(
            "sources",
            sources_type,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    # 5) `match_reason` — nullable string, set only when two sources merge.
    op.add_column(
        "shipper_candidates",
        sa.Column("match_reason", sa.String(255), nullable=True),
    )

    # 6) Partial-UNIQUE indexes so a repeat sighting of the same DOT/MC/OSM ref
    #    lands on the existing row, not a new one. Mirrors leads' MC/DOT/domain
    #    dedupe pattern. `postgresql_where` is a no-op on SQLite (dev/tests) —
    #    the on-disk index will be plain there, which is fine for a dev DB.
    op.create_index(
        "shipper_candidates_fmcsa_dot_key",
        "shipper_candidates",
        ["fmcsa_dot"],
        unique=True,
        postgresql_where=sa.text("fmcsa_dot IS NOT NULL"),
    )
    op.create_index(
        "shipper_candidates_fmcsa_mc_key",
        "shipper_candidates",
        ["fmcsa_mc"],
        unique=True,
        postgresql_where=sa.text("fmcsa_mc IS NOT NULL"),
    )
    op.create_index(
        "shipper_candidates_osm_ref_key",
        "shipper_candidates",
        ["osm_ref"],
        unique=True,
        postgresql_where=sa.text("osm_ref IS NOT NULL"),
    )


def downgrade() -> None:
    # Reverse order — indexes off first, then columns, then restore `source`
    # + CHECK + its index, then widen `id` back to 96.
    op.drop_index("shipper_candidates_osm_ref_key", table_name="shipper_candidates")
    op.drop_index("shipper_candidates_fmcsa_mc_key", table_name="shipper_candidates")
    op.drop_index("shipper_candidates_fmcsa_dot_key", table_name="shipper_candidates")

    op.drop_column("shipper_candidates", "match_reason")
    op.drop_column("shipper_candidates", "sources")
    op.drop_column("shipper_candidates", "osm_ref")
    op.drop_column("shipper_candidates", "fmcsa_mc")
    op.drop_column("shipper_candidates", "fmcsa_dot")

    op.alter_column(
        "shipper_candidates",
        "id",
        existing_type=sa.String(32),
        type_=sa.String(96),
        existing_nullable=False,
    )

    # Restore `source` — table is empty on rollback, so NOT NULL without a
    # server_default is safe (no existing rows to back-fill).
    op.add_column("shipper_candidates", sa.Column("source", sa.String(16), nullable=False))
    op.create_check_constraint(
        "shipper_candidates_source_check",
        "shipper_candidates",
        "source IN ('FMCSA','OSM')",
    )
    op.create_index("shipper_candidates_source", "shipper_candidates", ["source"])
