"""owner account: hardcode email + password, drop SEED_OWNER_PASSWORD env dependency

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-08

Per the 2026-10-08 user call: the demo login account lives in the DB, not in
env. The seeded owner from migration 0008 (``owner@ljm-demo.local`` with an
env-sourced password) is rewritten to ``owner@ljm.com`` / ``password`` on the
same ULID (``01OWNERLJMDEMOACCOUNTSEED1``), so every foreign-key reference —
``organization_members.user_id``, ``tenant_id`` backfill from 0016, anything
else that pins to the owner's id — keeps pointing at the same row.

Shape:
  * UPDATE the existing seeded owner row in place when it still carries the
    historical email, swapping email + password_hash and KEEPING the id.
  * INSERT the row if it is missing (fresh DB that somehow skipped 0008's
    seed, or a hand-wiped users table) — same id, email, name, hash, with
    ``role='owner'`` and ``is_active=TRUE``. Tenant backfill happens in 0016
    via `UPDATE ... WHERE tenant_id IS NULL`, which this UPDATE preserves
    (we never touch tenant_id, so 0016's assignment stays intact).

Password hashing uses the same argon2id params as 0008 (time_cost=3,
memory_cost=65536, parallelism=1) so verify_password behaves identically.

Postgres + SQLite safe (ON CONFLICT vs INSERT OR IGNORE).
Downgrade: restore email to ``owner@ljm-demo.local`` on the same row; leave
the password hash alone (there is no reversible-source to revert to).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OWNER_ID = "01OWNERLJMDEMOACCOUNTSEED1"
_OWNER_EMAIL = "owner@ljm.com"
_OWNER_NAME = "LJM Owner"
_OWNER_PASSWORD = "password"
_LEGACY_EMAIL = "owner@ljm-demo.local"


def _hash() -> str:
    # Keep in lockstep with migration 0008 + app.auth.passwords. argon2-cffi's
    # defaults are already in this ballpark but we pin the OWASP numbers so a
    # future lib default change can't silently weaken this one seeded hash.
    return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1).hash(_OWNER_PASSWORD)


def upgrade() -> None:
    bind = op.get_bind()
    pw_hash = _hash()

    # In-place update by id — the stable ULID keeps every FK (membership,
    # tenant backfill, future per-user rows) pointing at the same row.
    res = bind.execute(
        sa.text(
            "UPDATE users SET email = :email, password_hash = :hash, name = :name, "
            "role = 'owner', is_active = TRUE WHERE id = :id"
        ),
        {"id": _OWNER_ID, "email": _OWNER_EMAIL, "name": _OWNER_NAME, "hash": pw_hash},
    )
    if res.rowcount and res.rowcount > 0:
        return

    # Fallback: owner row missing (fresh DB that skipped 0008's seed or a
    # hand-wiped users table). Insert idempotently; tenant_id is left NULL and
    # 0016's backfill assigns LJM's tenant on upgrade. For a DB that already
    # ran 0016, we set tenant_id explicitly so RLS still sees the row.
    tenant_id = "01LJMORGLJM00000000000000A"
    dialect = bind.dialect.name
    if dialect == "postgresql":
        bind.execute(
            sa.text(
                "INSERT INTO users (id, tenant_id, email, name, role, password_hash, is_active) "
                "VALUES (:id, :tid, :email, :name, 'owner', :hash, TRUE) "
                "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, "
                "password_hash = EXCLUDED.password_hash, name = EXCLUDED.name, "
                "role = EXCLUDED.role, is_active = EXCLUDED.is_active"
            ),
            {"id": _OWNER_ID, "tid": tenant_id, "email": _OWNER_EMAIL, "name": _OWNER_NAME, "hash": pw_hash},
        )
    else:
        bind.execute(
            sa.text(
                "INSERT OR REPLACE INTO users "
                "(id, tenant_id, email, name, role, password_hash, is_active) "
                "VALUES (:id, :tid, :email, :name, 'owner', :hash, 1)"
            ),
            {"id": _OWNER_ID, "tid": tenant_id, "email": _OWNER_EMAIL, "name": _OWNER_NAME, "hash": pw_hash},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE users SET email = :email WHERE id = :id"),
        {"id": _OWNER_ID, "email": _LEGACY_EMAIL},
    )
