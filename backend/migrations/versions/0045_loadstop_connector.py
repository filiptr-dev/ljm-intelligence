"""LoadStop connector — settings columns + ingest-run log

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-11

Adds the LoadStop TMS connector surface:

* ``settings.loads_loadstop_driver`` — ``'off' | 'api'`` (no ``'agent'``;
  LoadStop uses plain httpx, not Playwright). Server default ``'off'`` so
  the existing LJM row doesn't trip the NOT NULL.
* ``settings.loadstop_import_fleet | _customers | _loads | _rates`` —
  per-service toggle, default ``true`` (operator can turn any off from
  Settings once Test is green).
* ``loadstop_ingest_runs`` — one row per ingest service invocation. The
  Settings page reads it to render "Last sync (ok · N rows · 2m ago)".

Credentials themselves go through the shared ``tenant_credentials`` vault
(connectors ``loadstop_login`` / ``loadstop_session``) — no schema change
needed there; the vault is dict-shaped.

Downgrade drops the three toggles + the driver column + the run table.
Data loss on the toggles is cosmetic (defaults are ``true``); the vault
rows persist across a downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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

    op.create_table(
        "loadstop_ingest_runs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=26), nullable=False),
        sa.Column("service", sa.String(length=48), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'running'")),
        sa.Column("rows_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_upserted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Index("ix_loadstop_runs_service_started", "service", "started_at"),
    )


def downgrade() -> None:
    op.drop_table("loadstop_ingest_runs")
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("loadstop_import_rates")
        batch.drop_column("loadstop_import_loads")
        batch.drop_column("loadstop_import_customers")
        batch.drop_column("loadstop_import_fleet")
        batch.drop_column("loads_loadstop_driver")
