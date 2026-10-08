"""users table + one seeded owner + settings.auth_jwt_secret backfill

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01

Simplest-useful login per plan amendments 2 + 3:
  * ONE account, seeded here: ``owner@ljm.com`` with password ``password``.
    Per the 2026-10-08 user call the demo credentials live in the DB, not
    in env — a fresh DB works with no env var set. The historical
    ``SEED_OWNER_PASSWORD`` indirection has been removed; migration 0023
    rewrites the email + hash on upgrade for DBs that still carry the
    pre-change seed.
  * No ``must_change_password``, no password-change route, no team UI, no
    rate-limit table, no refresh-token table. The access JWT is long-lived
    (default 7 days) and stateless; logout clears the session cookie on the
    Vercel origin. Simplest form still-secure for v1.
  * The JWT signing secret is minted here and parked on the singleton
    ``settings`` row (same durable-secret-in-DB pattern as
    ``settings.unsubscribe_secret`` in 0007). An optional ``AUTH_JWT_SECRET``
    env var still wins at read time (``effective_auth_jwt_secret``) so ops
    can rotate without a DB write.

Idempotency: the seed uses ``INSERT ... ON CONFLICT`` on Postgres, and
``INSERT OR IGNORE`` on SQLite — a re-run never overwrites the owner's
password after they've set a real one. Downgrade drops the whole table.
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep in sync with ``app.auth.passwords`` — the two are tiny on purpose.
_SEED_EMAIL = "owner@ljm.com"
_SEED_NAME = "LJM Owner"
_SEED_PASSWORD = "password"
# A plain 26-char uppercase token stands in for a ULID so we don't drag a
# ULID lib into the migration layer; the format is "26 chars, URL-safe" which
# matches the project's existing ``shipper_candidates.id`` shape well enough.
_OWNER_ID = "01OWNERLJMDEMOACCOUNTSEED1"


def _now_argon2_hash(pw: str) -> str:
    # OWASP-current argon2id params; argon2-cffi's defaults are already in that
    # ballpark. Explicit values here so a future ``argon2-cffi`` default change
    # does not silently weaken the one seeded hash we ship.
    hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
    return hasher.hash(pw)


def upgrade() -> None:
    # --- users table ---------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default=sa.text("'owner'")),
        # Nullable so a future Google-SSO user — who has no local password —
        # can share this table without a schema change. Auth refuses NULL.
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("users_email", "users", ["email"], unique=True)

    # --- settings.auth_jwt_secret (same shape as 0007's unsubscribe_secret) --
    op.add_column("settings", sa.Column("auth_jwt_secret", sa.String(128), nullable=True))

    bind = op.get_bind()
    jwt_secret = secrets.token_urlsafe(48)
    bind.execute(
        sa.text(
            "UPDATE settings SET auth_jwt_secret = :tok "
            "WHERE id = 1 AND (auth_jwt_secret IS NULL OR auth_jwt_secret = '')"
        ),
        {"tok": jwt_secret},
    )

    # --- seed the single owner account --------------------------------------
    # Dialect-split: Postgres and SQLite both support an idempotent insert,
    # but spell it differently. We only ever run on these two.
    dialect = bind.dialect.name
    pw_hash = _now_argon2_hash(_SEED_PASSWORD)
    if dialect == "postgresql":
        bind.execute(
            sa.text(
                "INSERT INTO users (id, email, name, role, password_hash, is_active) "
                "VALUES (:id, :email, :name, 'owner', :hash, TRUE) "
                "ON CONFLICT (email) DO NOTHING"
            ),
            {"id": _OWNER_ID, "email": _SEED_EMAIL, "name": _SEED_NAME, "hash": pw_hash},
        )
    else:
        bind.execute(
            sa.text(
                "INSERT OR IGNORE INTO users (id, email, name, role, password_hash, is_active) "
                "VALUES (:id, :email, :name, 'owner', :hash, 1)"
            ),
            {"id": _OWNER_ID, "email": _SEED_EMAIL, "name": _SEED_NAME, "hash": pw_hash},
        )


def downgrade() -> None:
    op.drop_index("users_email", table_name="users")
    op.drop_table("users")
    op.drop_column("settings", "auth_jwt_secret")
