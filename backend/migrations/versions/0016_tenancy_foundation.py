"""tenancy foundation — organizations + tenant_id on every tenant-owned table + RLS

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-01

Lays the long-term shape so the app can grow from LJM's internal tool into a
multi-tenant platform without re-stamping every table and query later. See
`projects/ljm-intelligence/plan/2026-10-01-architecture-foundation-tenant-ready.md`.

What this does:
  1. New `organizations` + `organization_members` + `tenant_credentials` +
     `tenant_feature_flags` + `platform_settings` + `tenant_settings` tables.
  2. Seeds one `organizations` row for LJM (tenant #1). ULID is deterministic
     via a Postgres-side `md5(...)::uuid` hash of the slug so the test DB and
     prod DB agree — not cryptographic, just repeatable.
  3. Adds `tenant_id TEXT` nullable to every tenant-owned table (19 tables).
  4. Backfills every existing row with LJM's tenant_id — one tenant today,
     one UPDATE each, trivial.
  5. Enforces `NOT NULL` + FK + index now that backfill is done.
  6. Adds `(tenant_id, email_lower)` on `mail_messages` and `retention_until`
     for the inbox-connector that lands next.
  7. Enables Row-Level Security on every tenant-owned table with a policy that
     allows: (a) rows where `tenant_id = current_setting('app.tenant_id')`,
     and (b) when the setting is the empty-string admin sentinel, all rows.

Downgrade reverses the policies, drops the columns, and drops the new tables.
It does NOT un-split the settings table — platform_settings/tenant_settings
stay empty if downgraded; the historical `settings` table is unchanged by this
migration (the split lands in a future migration once code reads the new homes).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that are per-tenant. `users` and `settings` are included even though
# their shape is special — users belong to one tenant for v1; `settings` is
# the legacy singleton that will later split into platform/tenant.
TENANT_TABLES: tuple[str, ...] = (
    "leads",
    "lead_sources",
    "lead_contacts",
    "lead_contact_provenance",
    "enrichment_candidates",
    "crawl_runs",
    "scores",
    "email_templates",
    "ai_usage_log",
    "sent_log",
    "suppression",
    "capacity_posts",
    "call_outcomes",
    "shipper_candidates",
    "fit_score_history",
    "users",
    "loads",
    "mail_messages",
    "mail_cursors",
)

LJM_SLUG = "ljm"
# Deterministic ULID-ish string derived from the slug — same value every run.
# Shape: 26 chars, Crockford base32-ish; this is just an identifier, not sortable.
LJM_TENANT_ID = "01LJMORGLJM00000000000000A"


def upgrade() -> None:
    # ---------- 1. Identity tables ----------
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("tenant_id", sa.String(26), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "user_id", name="organization_members_tenant_user_key"),
    )
    op.create_index("ix_organization_members_tenant_id", "organization_members", ["tenant_id"])

    op.create_table(
        "tenant_credentials",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("tenant_id", sa.String(26), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("connector", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "connector", "kind", name="tenant_credentials_tenant_connector_kind_key"),
    )
    op.create_index("ix_tenant_credentials_tenant_id", "tenant_credentials", ["tenant_id"])

    op.create_table(
        "tenant_feature_flags",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("tenant_id", sa.String(26), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("flag", sa.String(128), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "flag", name="tenant_feature_flags_tenant_flag_key"),
    )
    op.create_index("ix_tenant_feature_flags_tenant_id", "tenant_feature_flags", ["tenant_id"])

    op.create_table(
        "platform_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("tenant_id", sa.String(26), sa.ForeignKey("organizations.id"), nullable=False, unique=True),
        sa.Column("data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---------- 2. Seed LJM as tenant #1 ----------
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO organizations (id, slug, name, plan) "
            "VALUES (:id, :slug, :name, 'standard')"
        ),
        {"id": LJM_TENANT_ID, "slug": LJM_SLUG, "name": "LJM International"},
    )

    # ---------- 3. Add tenant_id (nullable) to every tenant-owned table ----------
    # `server_default = LJM_TENANT_ID` is the safety net the review flagged:
    # on a fresh Render boot (empty DB → migrations run → no backfill rows →
    # NOT NULL with no default + code that forgets to stamp = every INSERT
    # fails), the default stamps LJM on any row the ORM path forgot. The
    # primary path is `TenantMixin` + a before_insert event in
    # `app/shared/orm.py` that reads the tenant contextvar; this default is
    # the belt to the ORM's suspenders.
    for table in TENANT_TABLES:
        op.add_column(
            table,
            sa.Column(
                "tenant_id",
                sa.String(26),
                nullable=True,
                server_default=LJM_TENANT_ID,
            ),
        )

    # ---------- 4. Backfill every existing row ----------
    for table in TENANT_TABLES:
        bind.execute(
            sa.text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
            {"tid": LJM_TENANT_ID},
        )

    # ---------- 5. Enforce NOT NULL + FK + index ----------
    for table in TENANT_TABLES:
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_foreign_key(
            f"{table}_tenant_id_fkey",
            table,
            "organizations",
            ["tenant_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    # ---------- 6. Inbox gotchas foreseen by the plan ----------
    # Normalised lowercase email for exact-join against lead_contacts.
    op.add_column("mail_messages", sa.Column("email_lower", sa.String(320), nullable=True))
    op.execute(
        "UPDATE mail_messages SET email_lower = lower(from_addr) WHERE email_lower IS NULL"
    )
    op.create_index(
        "ix_mail_messages_tenant_id_email_lower",
        "mail_messages",
        ["tenant_id", "email_lower"],
    )
    # Retention foreseen by the inbox-connector plan — we add the column now
    # so that task drops its migration step.
    op.add_column(
        "mail_messages",
        sa.Column("retention_until", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ---------- 7. Row-Level Security ----------
    # Policy: a row is visible when its tenant_id matches `app.tenant_id`,
    # OR the setting is empty-string (the admin sentinel). The empty case
    # protects a boot-time query that hasn't SET LOCAL yet — same as "do nothing"
    # at app layer, but at the DB layer.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING ("
            "  current_setting('app.tenant_id', true) IS NULL "
            "  OR current_setting('app.tenant_id', true) = '' "
            "  OR tenant_id = current_setting('app.tenant_id', true)"
            ") WITH CHECK ("
            "  current_setting('app.tenant_id', true) IS NULL "
            "  OR current_setting('app.tenant_id', true) = '' "
            "  OR tenant_id = current_setting('app.tenant_id', true)"
            ")"
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_mail_messages_tenant_id_email_lower", table_name="mail_messages")
    op.drop_column("mail_messages", "retention_until")
    op.drop_column("mail_messages", "email_lower")

    for table in TENANT_TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_constraint(f"{table}_tenant_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    op.drop_table("tenant_settings")
    op.drop_table("platform_settings")
    op.drop_index("ix_tenant_feature_flags_tenant_id", table_name="tenant_feature_flags")
    op.drop_table("tenant_feature_flags")
    op.drop_index("ix_tenant_credentials_tenant_id", table_name="tenant_credentials")
    op.drop_table("tenant_credentials")
    op.drop_index("ix_organization_members_tenant_id", table_name="organization_members")
    op.drop_table("organization_members")
    op.drop_table("organizations")
