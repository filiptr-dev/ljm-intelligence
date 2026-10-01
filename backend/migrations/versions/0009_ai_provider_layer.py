"""ai provider layer — ai_features on settings + ai_usage_log table

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-01

Implements the `ai-provider-layer-claude` plan:

  * `settings.ai_features` — JSONB (Postgres) / JSON (SQLite) column holding
    the per-feature `{provider, model}` matrix. Nullable; code falls back to
    `app.sources.provider.DEFAULT_FEATURES` when unset or when a key is bad.
  * `ai_usage_log` — one row per adapter call. Powers the Settings cost strip
    and the compare-view history. Prompt bodies are NEVER written; only
    `input_hash` (sha256 of prompt) is kept for coarse analytics.

Why on the existing settings row (not a new `ai_providers` table): one owner,
one config — a dedicated table is ceremony for a single-user product.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb_type():
    """JSONB on Postgres, JSON on SQLite (tests)."""
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("settings", sa.Column("ai_features", _jsonb_type(), nullable=True))

    op.create_table(
        "ai_usage_log",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("feature", sa.String(48), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("compare_id", sa.String(32), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ai_usage_log_created", "ai_usage_log", [sa.text("created_at DESC")])
    op.create_index("ai_usage_log_feature_created", "ai_usage_log", ["feature", sa.text("created_at DESC")])
    op.create_index("ai_usage_log_provider_created", "ai_usage_log", ["provider", sa.text("created_at DESC")])
    op.create_index("ai_usage_log_compare_id", "ai_usage_log", ["compare_id"])


def downgrade() -> None:
    op.drop_index("ai_usage_log_compare_id", table_name="ai_usage_log")
    op.drop_index("ai_usage_log_provider_created", table_name="ai_usage_log")
    op.drop_index("ai_usage_log_feature_created", table_name="ai_usage_log")
    op.drop_index("ai_usage_log_created", table_name="ai_usage_log")
    op.drop_table("ai_usage_log")
    op.drop_column("settings", "ai_features")
