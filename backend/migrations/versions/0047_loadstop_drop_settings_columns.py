"""LoadStop: drop driver + toggle columns; purge vaulted login creds.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-12

LoadStop credentials now come from env (``LOADSTOP_USERNAME`` /
``LOADSTOP_PASSWORD`` — user override of the standing secrets-in-DB rule),
and the ingest runs once a day plus on-demand via per-page "Sync" buttons.
The Settings-UI write path is gone, so the columns that used to drive it
go with it.

Also purges the stored login bundle at
``tenant_credentials WHERE connector='loadstop_login'`` — passwords live
in env now. The rotating session cookie row at
``connector='loadstop_session'`` stays; auto-relogin on 401 refreshes it.

Downgrade re-adds the columns with the original server defaults so a
rollback leaves a valid shape; the login vault bundle is NOT recreated
(we don't own a password to put in it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("loadstop_import_rates")
        batch.drop_column("loadstop_import_loads")
        batch.drop_column("loadstop_import_customers")
        batch.drop_column("loadstop_import_fleet")
        batch.drop_column("loads_loadstop_driver")

    # Purge the stored password bundle — creds are env-only from here on.
    op.execute("DELETE FROM tenant_credentials WHERE connector='loadstop_login'")


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "loads_loadstop_driver",
                sa.String(length=8),
                nullable=False,
                server_default=sa.text("'off'"),
            )
        )
        batch.add_column(
            sa.Column(
                "loadstop_import_fleet",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )
        batch.add_column(
            sa.Column(
                "loadstop_import_customers",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )
        batch.add_column(
            sa.Column(
                "loadstop_import_loads",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )
        batch.add_column(
            sa.Column(
                "loadstop_import_rates",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )
