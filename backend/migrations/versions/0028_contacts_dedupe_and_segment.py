"""Contacts dedupe + segment + FMCSA snapshot cache

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-08

Freight-manager contacts plan — projects/ljm-intelligence/plan/
2026-10-08-freight-manager-contacts.md. Three additive changes:

  * ``lead_contacts.email_norm`` — ``lower(email)``. Written on insert/update
    by the service (works on PG + SQLite); backfilled here from existing
    rows. Partial-unique dedupe seam.
  * ``lead_contacts.name_norm`` — lowercased, whitespace-collapsed name.
    Written by the service; backfilled here. Powers name-only dedupe when
    an email isn't published yet.
  * Partial unique indexes on ``(tenant_id, lead_id, email_norm)`` where
    ``email IS NOT NULL`` and ``(tenant_id, lead_id, name_norm)`` where
    ``email IS NULL AND name IS NOT NULL``. The index IS the dedupe
    guarantee — a second ingester can't bypass it.
  * ``fmcsa_snapshot_cache`` — tiny ``(dot, payload jsonb, fetched_at)``
    key-value with 30-day TTL enforced by the caller. Keeps the
    per-lead FMCSA snapshot fetch cheap on repeat refreshes.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_WS_RE = re.compile(r"\s+")


def _norm_name(name: object) -> str | None:
    if not name:
        return None
    s = str(name)
    # NFKD-fold + strip diacritics, lowercase, collapse whitespace.
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _WS_RE.sub(" ", s).strip().lower()
    return s or None


def _jsonb():
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ---------- lead_contacts: email_norm + name_norm ----------
    with op.batch_alter_table("lead_contacts") as b:
        b.add_column(sa.Column("email_norm", sa.String(255)))
        b.add_column(sa.Column("name_norm", sa.String(255)))

    # Backfill
    rows = bind.execute(
        sa.text("SELECT id, email, name FROM lead_contacts")
    ).fetchall()
    for r in rows:
        rid, email, name = r[0], r[1], r[2]
        email_norm = email.strip().lower() if email else None
        name_norm = _norm_name(name)
        if email_norm is None and name_norm is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE lead_contacts SET email_norm = :e, name_norm = :n WHERE id = :id"
            ),
            {"e": email_norm, "n": name_norm, "id": rid},
        )

    # Partial unique indexes.
    if is_pg:
        op.execute(
            "CREATE UNIQUE INDEX lead_contacts_lead_email_u "
            "ON lead_contacts (tenant_id, lead_id, email_norm) "
            "WHERE email IS NOT NULL"
        )
        op.execute(
            "CREATE UNIQUE INDEX lead_contacts_lead_name_u "
            "ON lead_contacts (tenant_id, lead_id, name_norm) "
            "WHERE email IS NULL AND name IS NOT NULL"
        )
    else:
        # SQLite: partial unique indexes supported with WHERE clause.
        op.execute(
            "CREATE UNIQUE INDEX lead_contacts_lead_email_u "
            "ON lead_contacts (tenant_id, lead_id, email_norm) "
            "WHERE email IS NOT NULL"
        )
        op.execute(
            "CREATE UNIQUE INDEX lead_contacts_lead_name_u "
            "ON lead_contacts (tenant_id, lead_id, name_norm) "
            "WHERE email IS NULL AND name IS NOT NULL"
        )

    # ---------- fmcsa_snapshot_cache ----------
    op.create_table(
        "fmcsa_snapshot_cache",
        sa.Column("dot", sa.String(32), primary_key=True),
        sa.Column("payload", _jsonb(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "fmcsa_snapshot_cache_fetched_at_idx",
        "fmcsa_snapshot_cache",
        ["fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("fmcsa_snapshot_cache_fetched_at_idx", table_name="fmcsa_snapshot_cache")
    op.drop_table("fmcsa_snapshot_cache")

    op.execute("DROP INDEX IF EXISTS lead_contacts_lead_name_u")
    op.execute("DROP INDEX IF EXISTS lead_contacts_lead_email_u")

    with op.batch_alter_table("lead_contacts") as b:
        b.drop_column("name_norm")
        b.drop_column("email_norm")
