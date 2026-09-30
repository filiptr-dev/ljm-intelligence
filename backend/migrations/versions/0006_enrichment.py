"""enrichment — LLM-scraped decision-makers + website contacts + auto-outreach settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30

Additive migration for the LLM-enrichment scraper plan
(`projects/ljm-intelligence/plan/2026-09-30-llm-scraper.md`).

New tables:
  - lead_contact_provenance — per-sighting evidence for a `lead_contacts` row.
  - enrichment_candidates   — pre-promotion contacts on `shipper_candidates`.

New columns on `lead_contacts`:
  source_url, is_decision_maker, linkedin_url, evidence, last_verified_at,
  confidence, pipeline_status, pipeline_status_at.

New columns on `sent_log`:
  contact_id (FK lead_contacts.id ON DELETE SET NULL).

New columns on `leads` / `shipper_candidates`:
  last_enriched_at, enrichment_status, enrichment_error, is_js_only_site,
  linkedin_company_url, website_url.

New columns on `settings` (auto-outreach — OFF by default):
  auto_outreach_enabled, auto_outreach_template_id, auto_outreach_daily_cap,
  auto_outreach_window_start_h, auto_outreach_window_end_h,
  auto_outreach_status_filter.

Downgrade drops every new column/table cleanly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb_type():
    """JSONB on Postgres, JSON on SQLite (tests)."""
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # ---- lead_contacts new columns ----
    op.add_column("lead_contacts", sa.Column("source_url", sa.String(500), nullable=True))
    op.add_column(
        "lead_contacts",
        sa.Column("is_decision_maker", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("lead_contacts", sa.Column("linkedin_url", sa.String(500), nullable=True))
    op.add_column("lead_contacts", sa.Column("evidence", _jsonb_type(), nullable=True))
    op.add_column("lead_contacts", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lead_contacts", sa.Column("confidence", sa.String(16), nullable=True))
    op.add_column(
        "lead_contacts",
        sa.Column("pipeline_status", sa.String(16), nullable=False, server_default=sa.text("'found'")),
    )
    op.add_column("lead_contacts", sa.Column("pipeline_status_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("lead_contacts_lead_dm", "lead_contacts", ["lead_id", "is_decision_maker"])
    op.create_index("lead_contacts_lead_email", "lead_contacts", ["lead_id", "email"])
    op.create_index("lead_contacts_pipeline_status", "lead_contacts", ["pipeline_status"])

    # ---- sent_log new column ----
    op.add_column(
        "sent_log",
        sa.Column("contact_id", sa.BigInteger(), sa.ForeignKey("lead_contacts.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("sent_log_contact_sent", "sent_log", ["contact_id", sa.text("sent_at DESC")])

    # ---- leads new columns ----
    op.add_column("leads", sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("enrichment_status", sa.String(32), nullable=True))
    op.add_column("leads", sa.Column("enrichment_error", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column("is_js_only_site", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("leads", sa.Column("linkedin_company_url", sa.String(500), nullable=True))
    op.add_column("leads", sa.Column("website_url", sa.String(500), nullable=True))

    # ---- shipper_candidates new columns ----
    op.add_column("shipper_candidates", sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shipper_candidates", sa.Column("enrichment_status", sa.String(32), nullable=True))
    op.add_column("shipper_candidates", sa.Column("enrichment_error", sa.Text(), nullable=True))
    op.add_column(
        "shipper_candidates",
        sa.Column("is_js_only_site", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("shipper_candidates", sa.Column("linkedin_company_url", sa.String(500), nullable=True))
    op.add_column("shipper_candidates", sa.Column("website_url", sa.String(500), nullable=True))

    # ---- lead_contact_provenance table ----
    op.create_table(
        "lead_contact_provenance",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "contact_id",
            sa.BigInteger(),
            sa.ForeignKey("lead_contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("citations", _jsonb_type(), nullable=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "lead_contact_provenance_contact_disc",
        "lead_contact_provenance",
        ["contact_id", sa.text("discovered_at DESC")],
    )

    # ---- enrichment_candidates table ----
    op.create_table(
        "enrichment_candidates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "candidate_id",
            sa.String(32),
            sa.ForeignKey("shipper_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("linkedin_url", sa.String(500), nullable=True),
        sa.Column("is_decision_maker", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("evidence", _jsonb_type(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("enrichment_candidates_cand_dm", "enrichment_candidates", ["candidate_id", "is_decision_maker"])

    # ---- settings — auto-outreach knobs ----
    op.add_column(
        "settings",
        sa.Column("auto_outreach_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "settings",
        sa.Column(
            "auto_outreach_template_id",
            sa.String(64),
            sa.ForeignKey("email_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "settings",
        sa.Column("auto_outreach_daily_cap", sa.Integer(), nullable=False, server_default=sa.text("20")),
    )
    op.add_column(
        "settings",
        sa.Column("auto_outreach_window_start_h", sa.Integer(), nullable=False, server_default=sa.text("8")),
    )
    op.add_column(
        "settings",
        sa.Column("auto_outreach_window_end_h", sa.Integer(), nullable=False, server_default=sa.text("18")),
    )
    op.add_column(
        "settings",
        sa.Column("auto_outreach_status_filter", sa.String(16), nullable=False, server_default=sa.text("'found'")),
    )
    op.add_column("settings", sa.Column("fit_weights", _jsonb_type(), nullable=True))

    # ---- fit score columns + history table ----
    op.add_column("leads", sa.Column("fit_score", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("fit_reasons", _jsonb_type(), nullable=True))
    op.add_column("leads", sa.Column("fit_computed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shipper_candidates", sa.Column("fit_score", sa.Integer(), nullable=True))
    op.add_column("shipper_candidates", sa.Column("fit_reasons", _jsonb_type(), nullable=True))
    op.add_column("shipper_candidates", sa.Column("fit_computed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "fit_score_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.String(64), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "candidate_id",
            sa.String(32),
            sa.ForeignKey("shipper_candidates.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasons", _jsonb_type(), nullable=True),
        sa.Column("signals", _jsonb_type(), nullable=True),
        sa.Column("weights", _jsonb_type(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("fit_score_history")
    op.drop_column("shipper_candidates", "fit_computed_at")
    op.drop_column("shipper_candidates", "fit_reasons")
    op.drop_column("shipper_candidates", "fit_score")
    op.drop_column("leads", "fit_computed_at")
    op.drop_column("leads", "fit_reasons")
    op.drop_column("leads", "fit_score")
    op.drop_column("settings", "fit_weights")
    # settings
    op.drop_column("settings", "auto_outreach_status_filter")
    op.drop_column("settings", "auto_outreach_window_end_h")
    op.drop_column("settings", "auto_outreach_window_start_h")
    op.drop_column("settings", "auto_outreach_daily_cap")
    op.drop_column("settings", "auto_outreach_template_id")
    op.drop_column("settings", "auto_outreach_enabled")

    op.drop_index("enrichment_candidates_cand_dm", table_name="enrichment_candidates")
    op.drop_table("enrichment_candidates")

    op.drop_index("lead_contact_provenance_contact_disc", table_name="lead_contact_provenance")
    op.drop_table("lead_contact_provenance")

    op.drop_column("shipper_candidates", "website_url")
    op.drop_column("shipper_candidates", "linkedin_company_url")
    op.drop_column("shipper_candidates", "is_js_only_site")
    op.drop_column("shipper_candidates", "enrichment_error")
    op.drop_column("shipper_candidates", "enrichment_status")
    op.drop_column("shipper_candidates", "last_enriched_at")

    op.drop_column("leads", "website_url")
    op.drop_column("leads", "linkedin_company_url")
    op.drop_column("leads", "is_js_only_site")
    op.drop_column("leads", "enrichment_error")
    op.drop_column("leads", "enrichment_status")
    op.drop_column("leads", "last_enriched_at")

    op.drop_index("sent_log_contact_sent", table_name="sent_log")
    op.drop_column("sent_log", "contact_id")

    op.drop_index("lead_contacts_pipeline_status", table_name="lead_contacts")
    op.drop_index("lead_contacts_lead_email", table_name="lead_contacts")
    op.drop_index("lead_contacts_lead_dm", table_name="lead_contacts")
    op.drop_column("lead_contacts", "pipeline_status_at")
    op.drop_column("lead_contacts", "pipeline_status")
    op.drop_column("lead_contacts", "confidence")
    op.drop_column("lead_contacts", "last_verified_at")
    op.drop_column("lead_contacts", "evidence")
    op.drop_column("lead_contacts", "linkedin_url")
    op.drop_column("lead_contacts", "is_decision_maker")
    op.drop_column("lead_contacts", "source_url")
