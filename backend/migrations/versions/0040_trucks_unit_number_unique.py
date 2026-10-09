"""Trucks: a unit number is unique within a tenant (owner-managed fleet, 0040).

The owner can now add units by hand (``POST /fleet/trucks``); the unique
constraint is the backstop for the API's friendly 409 when two requests race.
The 0038 demo seed already has unique numbers, so this applies cleanly.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "trucks_tenant_id_unit_number_key"


def upgrade() -> None:
    op.create_unique_constraint(_NAME, "trucks", ["tenant_id", "unit_number"])


def downgrade() -> None:
    op.drop_constraint(_NAME, "trucks", type_="unique")
