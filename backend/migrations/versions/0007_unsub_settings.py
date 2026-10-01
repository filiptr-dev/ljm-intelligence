"""unsub-settings + auto-outreach min-fit — persist unsubscribe knobs + fit threshold

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30

Moves the CAN-SPAM unsubscribe knobs (secret + public base URL) from optional
env vars into the `settings` row so the operator can configure them from the
Settings UI without a Render env-var round-trip. The env vars still win when
present (see `app.services.unsub_config.effective_unsub`) — this migration
only adds durable columns + backfills a fresh HMAC secret on row `id=1` so
first-boot doesn't leave the app with a null secret.

Why backfill in the migration
-----------------------------
An ephemeral, per-process secret would silently invalidate every outstanding
unsubscribe link on the next redeploy — worse than not having one. Minting
the secret exactly once, in the migration, gives ops a durable value on day
one; rotate lives outside v1.
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("unsubscribe_secret", sa.String(64), nullable=True))
    op.add_column("settings", sa.Column("unsubscribe_base_url", sa.String(500), nullable=True))
    # Auto-outreach now filters on the deterministic fit score (0..100). Default
    # 60 = "solid signal on at least a couple of dimensions." Ops can lower it
    # to widen the funnel or raise it to only auto-contact high-confidence
    # leads. See app/scoring/fit_score.py for what actually moves the number.
    op.add_column(
        "settings",
        sa.Column("auto_outreach_min_fit", sa.Integer(), nullable=False, server_default=sa.text("60")),
    )
    # One-time backfill of the singleton row's secret. Idempotent: only writes
    # if the column is still null (a re-run on a partially-upgraded DB won't
    # rotate an existing token).
    bind = op.get_bind()
    token = secrets.token_urlsafe(32)
    bind.execute(
        sa.text(
            "UPDATE settings SET unsubscribe_secret = :tok "
            "WHERE id = 1 AND (unsubscribe_secret IS NULL OR unsubscribe_secret = '')"
        ),
        {"tok": token},
    )


def downgrade() -> None:
    op.drop_column("settings", "auto_outreach_min_fit")
    op.drop_column("settings", "unsubscribe_base_url")
    op.drop_column("settings", "unsubscribe_secret")
