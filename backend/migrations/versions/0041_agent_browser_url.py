"""Agent-browser sidecar URL on settings row — DB-backed override for env.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-09

Standing project rule: creds/URLs live in the DB via the Settings UI, with
the existing env > DB settings-row overlay. ``AGENT_BROWSER_URL`` env still
wins when set; otherwise this column drives the loads agent-loop. The
shared secret (``AGENT_BROWSER_TOKEN``) lives in the vault as a connector
named ``agent_browser`` so it stays encrypted at rest and never comes back
in plaintext on a GET.

Server default empty string so adding the column never trips NOT NULL on
an existing row — the bbunikoop boolean-default lesson pinned in the
honeypot.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as b:
        b.add_column(
            sa.Column(
                "agent_browser_url",
                sa.String(500),
                nullable=False,
                server_default=sa.text("''"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as b:
        b.drop_column("agent_browser_url")
