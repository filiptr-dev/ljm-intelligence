"""owner account: rotate hardcoded password to Admin123!@#LJM

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-08

Per the 2026-10-08 user call: the demo login account's password is rotated
from the throwaway ``password`` seeded in 0023 to ``Admin123!@#LJM``. Email
and ULID stay as-is (``owner@ljm.com`` / ``01OWNERLJMDEMOACCOUNTSEED1``) so
every FK that pins to the owner keeps resolving to the same row.

Hash with the same argon2id params as 0008 + 0023 (time_cost=3,
memory_cost=65536, parallelism=1) so ``verify_password`` behaves identically.

Downgrade is a no-op: argon2id is one-way, there is no reversible source for
the previous hash and re-hashing ``password`` would be a credential leak in
the migration history. Intentionally left as a documented no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OWNER_ID = "01OWNERLJMDEMOACCOUNTSEED1"
_OWNER_PASSWORD = "Admin123!@#LJM"


def _hash() -> str:
    # Keep in lockstep with migrations 0008 + 0023 and app.auth.passwords.
    return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1).hash(_OWNER_PASSWORD)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE users SET password_hash = :hash WHERE id = :id"),
        {"id": _OWNER_ID, "hash": _hash()},
    )


def downgrade() -> None:
    # Argon2id is one-way; there is no reversible source for the previous
    # hash. Leaving the current hash in place is safer than re-seeding a
    # known-weak password in the migration history.
    pass
