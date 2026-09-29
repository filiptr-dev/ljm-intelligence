"""baseline — leads, sources, contacts, runs, scores, templates, settings, sent_log, suppression

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("mc", sa.String(32)),
        sa.Column("dot", sa.String(32)),
        sa.Column("domain", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("city", sa.String(128)),
        sa.Column("address", sa.String(255)),
        sa.Column("phone", sa.String(64)),
        sa.Column("primary_email", sa.String(255)),
        sa.Column("current_score", sa.Integer),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("first_seen_run_id", sa.String(64)),
        sa.Column("last_seen_run_id", sa.String(64)),
        sa.Column("raw", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recommendations", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    # Partial-unique dedupe: MC → DOT → domain.
    op.create_index("leads_mc_key", "leads", ["mc"], unique=True, postgresql_where=sa.text("mc IS NOT NULL"))
    op.create_index("leads_dot_key", "leads", ["dot"], unique=True, postgresql_where=sa.text("dot IS NOT NULL"))
    op.create_index(
        "leads_domain_key", "leads", ["domain"], unique=True, postgresql_where=sa.text("domain IS NOT NULL")
    )
    op.create_index("leads_state_kind", "leads", ["state", "kind"])

    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("counts", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
    )

    op.create_table(
        "lead_sources",
        sa.Column("lead_id", sa.String(64), sa.ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("source_ref", sa.String(255), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )

    op.create_table(
        "lead_contacts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.String(64), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("title", sa.String(255)),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(64)),
        sa.Column("source", sa.String(64)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("lead_contacts_lead_id", "lead_contacts", ["lead_id"])

    op.create_table(
        "email_templates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tokens", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "scores",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.String(64), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("rationale", sa.Text()),
        sa.Column("signals", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("model", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("scores_lead_id_created_at", "scores", ["lead_id", "created_at"])

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("threshold", sa.Integer, server_default=sa.text("70"), nullable=False),
        sa.Column("auto_send_enabled", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("auto_send_template_id", sa.String(64), sa.ForeignKey("email_templates.id", ondelete="SET NULL")),
        sa.Column("tone", sa.String(32), server_default=sa.text("'warm-professional'"), nullable=False),
        sa.Column("daily_send_cap", sa.Integer, server_default=sa.text("50"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(128)),
    )
    # Seed the singleton row so /settings can always read one.
    op.execute("INSERT INTO settings (id) VALUES (1) ON CONFLICT DO NOTHING")

    op.create_table(
        "sent_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.String(64), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("crawl_runs.id", ondelete="SET NULL")),
        sa.Column("template_id", sa.String(64), sa.ForeignKey("email_templates.id", ondelete="SET NULL")),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("to_email", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255)),
        sa.Column("body", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
    )
    op.create_index("sent_log_lead_id_sent_at", "sent_log", ["lead_id", "sent_at"])
    op.create_index("sent_log_sent_at", "sent_log", ["sent_at"])

    op.create_table(
        "suppression",
        sa.Column("email", sa.String(255), primary_key=True),
        sa.Column("reason", sa.String(255)),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("suppression")
    op.drop_index("sent_log_sent_at", table_name="sent_log")
    op.drop_index("sent_log_lead_id_sent_at", table_name="sent_log")
    op.drop_table("sent_log")
    op.drop_table("settings")
    op.drop_index("scores_lead_id_created_at", table_name="scores")
    op.drop_table("scores")
    op.drop_table("email_templates")
    op.drop_index("lead_contacts_lead_id", table_name="lead_contacts")
    op.drop_table("lead_contacts")
    op.drop_table("lead_sources")
    op.drop_table("crawl_runs")
    op.drop_index("leads_state_kind", table_name="leads")
    op.drop_index("leads_domain_key", table_name="leads")
    op.drop_index("leads_dot_key", table_name="leads")
    op.drop_index("leads_mc_key", table_name="leads")
    op.drop_table("leads")
