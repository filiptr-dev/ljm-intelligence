"""analysis — predictions, lookalikes, objections, forget-contact audit

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-01

Covers the inbox-analysis plan's analysis layer:

  * ``prediction_runs`` — one row per nightly fan-out run.
  * ``broker_predictions`` — win-prob + health + best-send-hour + churn +
    slow-payer flag + reply-speed-lift + first-touch latency per broker.
  * ``lane_predictions`` — p50/p75/p90 price + season hint per (origin,
    dest, equipment) lane.
  * ``broker_lookalikes`` — top-N peer domains for each broker.
  * ``objection_clusters`` — per-broker objection labels + exemplar.
  * ``forget_contact_audit`` — audit row for GDPR-style forget-contact.

Round-tripped on PG16 via tests/test_migrations_pg.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DEFAULT_TENANT = "01LJMORGLJM00000000000000A"


def _tenant_col() -> sa.Column:
    return sa.Column("tenant_id", sa.String(26), nullable=False, server_default=_DEFAULT_TENANT)


def upgrade() -> None:
    # ---- prediction_runs ------------------------------------------------
    op.create_table(
        "prediction_runs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        _tenant_col(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stats", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
                  nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_prediction_runs_tenant", "prediction_runs",
                    ["tenant_id", "kind", "started_at"])

    # ---- broker_predictions --------------------------------------------
    op.create_table(
        "broker_predictions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        _tenant_col(),
        sa.Column("broker_domain", sa.String(255), nullable=False),
        sa.Column("broker_name", sa.String(255), nullable=True),
        sa.Column("win_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("health_score", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("best_send_hour", sa.Integer(), nullable=True),
        sa.Column("is_slow_payer", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("churn_risk", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reply_speed_lift", sa.Float(), nullable=False, server_default="1"),
        sa.Column("first_touch_latency_days", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_broker_predictions_tenant", "broker_predictions",
        ["tenant_id", "broker_domain", "computed_at"],
    )

    # ---- lane_predictions ----------------------------------------------
    op.create_table(
        "lane_predictions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        _tenant_col(),
        sa.Column("origin", sa.String(128), nullable=False),
        sa.Column("dest", sa.String(128), nullable=False),
        sa.Column("equipment", sa.String(64), nullable=True),
        sa.Column("price_p50", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_p75", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_p90", sa.Numeric(10, 2), nullable=True),
        sa.Column("season_hint", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
                  nullable=False, server_default=sa.text("'{}'")),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_lane_predictions_tenant", "lane_predictions",
                    ["tenant_id", "origin", "dest"])

    # ---- broker_lookalikes ---------------------------------------------
    op.create_table(
        "broker_lookalikes",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        _tenant_col(),
        sa.Column("broker_domain", sa.String(255), nullable=False),
        sa.Column("peer_domain", sa.String(255), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_broker_lookalikes_tenant", "broker_lookalikes",
                    ["tenant_id", "broker_domain"])
    op.create_unique_constraint(
        "uq_broker_lookalike", "broker_lookalikes",
        ["tenant_id", "broker_domain", "peer_domain"],
    )

    # ---- objection_clusters --------------------------------------------
    op.create_table(
        "objection_clusters",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        _tenant_col(),
        sa.Column("broker_domain", sa.String(255), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exemplar", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_objection_clusters_tenant", "objection_clusters",
                    ["tenant_id", "broker_domain"])

    # ---- forget_contact_audit ------------------------------------------
    op.create_table(
        "forget_contact_audit",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        _tenant_col(),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("messages_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("insights_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("performed_by", sa.String(255), nullable=True),
        sa.Column("performed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_forget_contact_audit_tenant", "forget_contact_audit",
                    ["tenant_id", "performed_at"])

    # ---- RLS (Postgres only) -------------------------------------------
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in (
            "prediction_runs", "broker_predictions", "lane_predictions",
            "broker_lookalikes", "objection_clusters", "forget_contact_audit",
        ):
            bind.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            bind.exec_driver_sql(
                f"""
                CREATE POLICY {table}_tenant_isolation ON {table}
                USING (
                    tenant_id = current_setting('app.tenant_id', true)
                    OR coalesce(current_setting('app.tenant_id', true), '') = ''
                )
                WITH CHECK (
                    tenant_id = current_setting('app.tenant_id', true)
                    OR coalesce(current_setting('app.tenant_id', true), '') = ''
                )
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in (
            "prediction_runs", "broker_predictions", "lane_predictions",
            "broker_lookalikes", "objection_clusters", "forget_contact_audit",
        ):
            bind.exec_driver_sql(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
            bind.exec_driver_sql(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_forget_contact_audit_tenant", table_name="forget_contact_audit")
    op.drop_table("forget_contact_audit")

    op.drop_index("ix_objection_clusters_tenant", table_name="objection_clusters")
    op.drop_table("objection_clusters")

    op.drop_constraint("uq_broker_lookalike", "broker_lookalikes", type_="unique")
    op.drop_index("ix_broker_lookalikes_tenant", table_name="broker_lookalikes")
    op.drop_table("broker_lookalikes")

    op.drop_index("ix_lane_predictions_tenant", table_name="lane_predictions")
    op.drop_table("lane_predictions")

    op.drop_index("ix_broker_predictions_tenant", table_name="broker_predictions")
    op.drop_table("broker_predictions")

    op.drop_index("ix_prediction_runs_tenant", table_name="prediction_runs")
    op.drop_table("prediction_runs")
