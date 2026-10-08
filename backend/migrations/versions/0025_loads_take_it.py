"""Loads take-it + agent_runs + driver switch + vault cred_key

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-08

Adds the take-it / dedupe surface to ``loads``:

  * ``status`` (new | contacted | booked | lost)       — default 'new'.
  * ``status_at`` nullable timestamptz.
  * ``broker_lead_id`` nullable FK → ``leads.id`` ON DELETE SET NULL.
  * ``dedupe_group_hash`` — sha256[:32] of normalised
    ``(broker | o_state | d_state | pickup::date | equipment)``; backfilled
    row-by-row in the migration so existing rows read fine.

Adds ``settings`` driver dropdown + the ``load_source_urls`` JSON for
operator-managed public broker pages (reused by the ``ai_page`` source).

Creates ``agent_runs`` — one row per headless-agent run; the daily cap read
goes against this table.

All new NOT NULL columns carry server defaults so an existing populated row
needs zero backfill to pass the constraint (standing lesson: SQLite tests
hid a boolean-default bug once; this migration is explicit on PG side).
"""

from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb():
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def _norm(v: object) -> str:
    return str(v or "").strip().lower()


def _group_hash(broker: str, o_state: str, d_state: str, pickup_date, equipment: str) -> str:
    pickup = ""
    if pickup_date is not None:
        pickup = str(pickup_date)[:10]  # YYYY-MM-DD
    key = "|".join([_norm(broker), _norm(o_state), _norm(d_state), pickup, _norm(equipment)])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def upgrade() -> None:
    # ---------- loads: status + take-it ----------
    with op.batch_alter_table("loads") as b:
        b.add_column(
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'new'"),
            )
        )
        b.add_column(sa.Column("status_at", sa.DateTime(timezone=True)))
        b.add_column(sa.Column("broker_lead_id", sa.String(64)))
        b.add_column(
            sa.Column(
                "dedupe_group_hash",
                sa.String(32),
                nullable=False,
                server_default=sa.text("''"),
            )
        )

    # FK is created outside batch_alter_table so Postgres gets a proper constraint
    # (SQLite ignores FK anyway; the tests run under PG16 for this plan).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "loads_broker_lead_id_fkey",
            "loads",
            "leads",
            ["broker_lead_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index("loads_dedupe_group_idx", "loads", ["dedupe_group_hash"])
    op.create_index("loads_broker_lead_idx", "loads", ["broker_lead_id"])
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX loads_status_new_idx ON loads (status) WHERE status = 'new'"
        )
    else:
        op.create_index("loads_status_new_idx", "loads", ["status"])

    # Backfill dedupe_group_hash for any pre-existing rows.
    rows = bind.execute(
        sa.text(
            "SELECT id, broker_name, origin_state, dest_state, pickup_date, equipment FROM loads"
        )
    ).fetchall()
    for r in rows:
        gh = _group_hash(r[1] or "", r[2] or "", r[3] or "", r[4], r[5] or "")
        bind.execute(
            sa.text("UPDATE loads SET dedupe_group_hash = :gh WHERE id = :id"),
            {"gh": gh, "id": r[0]},
        )

    # ---------- settings: driver dropdowns + broker-page URLs ----------
    with op.batch_alter_table("settings") as b:
        b.add_column(
            sa.Column(
                "loads_dat_driver",
                sa.String(8),
                nullable=False,
                server_default=sa.text("'off'"),
            )
        )
        b.add_column(
            sa.Column(
                "loads_chr_driver",
                sa.String(8),
                nullable=False,
                server_default=sa.text("'off'"),
            )
        )
        b.add_column(
            sa.Column(
                "loads_lb123_driver",
                sa.String(8),
                nullable=False,
                server_default=sa.text("'off'"),
            )
        )
        b.add_column(
            sa.Column(
                "loads_truckstop_driver",
                sa.String(8),
                nullable=False,
                server_default=sa.text("'off'"),
            )
        )
        b.add_column(
            sa.Column(
                "load_source_urls",
                _jsonb(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        # Durable AES-GCM key for the CredentialVault — base64, 32 raw bytes.
        # Env TENANT_CRED_KEY wins; this column backfills so a deploy with no
        # env still has a working vault (same pattern as auth_jwt_secret).
        b.add_column(sa.Column("cred_key", sa.String(64)))

    # Backfill cred_key on the singleton settings row if unset. 32 random bytes
    # base64-encoded. Idempotent — only writes when NULL/empty.
    key = base64.b64encode(_secrets.token_bytes(32)).decode("ascii")
    bind.execute(
        sa.text(
            "UPDATE settings SET cred_key = :k WHERE id = 1 AND (cred_key IS NULL OR cred_key = '')"
        ),
        {"k": key},
    )
    # Also handle the "no row yet" edge: if the settings row doesn't exist, do
    # nothing — the app seeds it on first request and the vault helper writes
    # a key on demand. This mirrors the auth_jwt_secret bootstrap.

    # ---------- agent_runs ----------
    op.create_table(
        "agent_runs",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "tenant_id",
            sa.String(26),
            nullable=False,
            server_default=sa.text("'01LJMORGLJM00000000000000A'"),
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'ok'")),
        sa.Column("steps", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text()),
    )
    op.create_index("agent_runs_source_started_idx", "agent_runs", ["source", "started_at"])
    op.create_index("agent_runs_tenant_started_idx", "agent_runs", ["tenant_id", "started_at"])

    # RLS on new tenant-owned table.
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY")
        bind.exec_driver_sql("ALTER TABLE agent_runs FORCE ROW LEVEL SECURITY")
        bind.exec_driver_sql(
            "CREATE POLICY agent_runs_tenant_isolation ON agent_runs "
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
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql("DROP POLICY IF EXISTS agent_runs_tenant_isolation ON agent_runs")
        bind.exec_driver_sql("ALTER TABLE agent_runs DISABLE ROW LEVEL SECURITY")

    op.drop_index("agent_runs_tenant_started_idx", table_name="agent_runs")
    op.drop_index("agent_runs_source_started_idx", table_name="agent_runs")
    op.drop_table("agent_runs")

    with op.batch_alter_table("settings") as b:
        b.drop_column("cred_key")
        b.drop_column("load_source_urls")
        b.drop_column("loads_truckstop_driver")
        b.drop_column("loads_lb123_driver")
        b.drop_column("loads_chr_driver")
        b.drop_column("loads_dat_driver")

    op.drop_index("loads_status_new_idx", table_name="loads")
    op.drop_index("loads_broker_lead_idx", table_name="loads")
    op.drop_index("loads_dedupe_group_idx", table_name="loads")

    if bind.dialect.name == "postgresql":
        op.drop_constraint("loads_broker_lead_id_fkey", "loads", type_="foreignkey")

    with op.batch_alter_table("loads") as b:
        b.drop_column("dedupe_group_hash")
        b.drop_column("broker_lead_id")
        b.drop_column("status_at")
        b.drop_column("status")
