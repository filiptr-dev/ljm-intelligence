"""Load source vendor timestamps on settings

Revision ID: 0014
Revises: 0013

The ``loads.source`` column is already an open ``VARCHAR(32)`` (no CHECK constraint
emitted by 0010), so widening the vocabulary needs no DDL — it is a code concern.
Only the per-vendor ``<kind>_configured_at`` timestamps need storage.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as b:
        b.add_column(sa.Column("dat_configured_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("chr_configured_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("lb123_configured_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("truckstop_configured_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("settings") as b:
        b.drop_column("truckstop_configured_at")
        b.drop_column("lb123_configured_at")
        b.drop_column("chr_configured_at")
        b.drop_column("dat_configured_at")
