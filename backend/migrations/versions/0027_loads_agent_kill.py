"""Agent kill switch on settings row — DB-backed override for the registry.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-08

The per-source driver switch already lives on ``settings`` (migration 0025 —
``loads_dat_driver`` and friends). This slice completes the pattern by
adding the global kill switch to the DB too so an operator can flip the
agent off without a Render redeploy. Env ``LOADS_AGENT_KILL=1`` still wins
when set — mirrors the env > DB resolution the registry already uses for
the per-source driver.

Server default ``'off'`` so adding the column never trips the NOT NULL on an
existing populated row — the bbunikoop boolean-default lesson pinned in the
honeypot.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as b:
        b.add_column(
            sa.Column(
                "loads_agent_kill",
                sa.String(8),
                nullable=False,
                server_default=sa.text("'off'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as b:
        b.drop_column("loads_agent_kill")
