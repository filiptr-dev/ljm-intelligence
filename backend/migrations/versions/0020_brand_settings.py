"""brand settings — brand_* fields on the settings singleton

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-05

Adds the ~7 brand fields the rich email builder reads at send time:

  * brand_company, brand_fleet, brand_dispatcher, brand_phone, brand_email
    — the dispatcher-signature block the Design step toggles on/off.
  * brand_logo_url — absolute URL served to Gmail for the inline logo.
  * brand_accent_hex — default accent colour the builder prefills.

All nullable; existing rows read unchanged. Backend uses
`frontend/src/lib/data/types.ts:CLIENT` as the fallback shape so the
preview and the sent mail agree without a seed migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLS = [
    ("brand_company", sa.String(128)),
    ("brand_fleet", sa.String(128)),
    ("brand_dispatcher", sa.String(128)),
    ("brand_phone", sa.String(64)),
    ("brand_email", sa.String(320)),
    ("brand_logo_url", sa.String(500)),
    ("brand_accent_hex", sa.String(7)),
]


def upgrade() -> None:
    for name, col_type in _COLS:
        op.add_column("settings", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name, _ in _COLS:
        op.drop_column("settings", name)
