"""lead sources field backfill — per-field provenance on leads + backfill lead_contacts.source

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-01

Additive helper for the brokers-real-data plan:

  * ``leads.primary_email_source`` / ``leads.phone_source`` / ``leads.address_source``
    — nullable String(32) columns so future enrichment runs can mark each
    top-level broker/shipper contact string with its provenance (``FMCSA Census``
    / ``Gemini`` / ``Site Scraper`` / ``Manual``). We never write a default; a
    missing source renders as "—" in the UI.
  * Backfill ``lead_contacts.source`` for existing rows where NULL:
      - rows that have a ``source_url`` → ``'Site Scraper'`` (web discovery).
      - rows with no ``source_url`` → ``'FMCSA Census'`` (seeded from census).

Downgrade drops the three columns; the backfill isn't reversed (no reliable
"was it NULL before" marker, and leaving a `source` set is harmless).

Why only an additive helper (no schema restructure): the only new columns we
need belong on ``leads`` — the ``lead_contacts.source`` column already exists;
we just want it populated so the UI can show a badge per row. One UPDATE each.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("primary_email_source", sa.String(32), nullable=True))
    op.add_column("leads", sa.Column("phone_source", sa.String(32), nullable=True))
    op.add_column("leads", sa.Column("address_source", sa.String(32), nullable=True))

    # Idempotent backfill — guarded by `source IS NULL`.
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE lead_contacts SET source = 'Site Scraper' WHERE source IS NULL AND source_url IS NOT NULL")
    )
    bind.execute(
        sa.text("UPDATE lead_contacts SET source = 'FMCSA Census' WHERE source IS NULL AND source_url IS NULL")
    )


def downgrade() -> None:
    op.drop_column("leads", "address_source")
    op.drop_column("leads", "phone_source")
    op.drop_column("leads", "primary_email_source")
