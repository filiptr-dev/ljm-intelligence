"""identity models — SQLAlchemy tables rehomed from `app/models/__init__.py`.

Re-exported via `app.models` so Alembic autogenerate still sees the full
`Base.metadata`. New columns/indexes land here, not in the re-export.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.orm import JSONType, TenantMixin

# ---------- Platform / tenant identity tables (migration 0016) --------------
#
# These were created by migration 0016 but had no ORM model, so Alembic
# autogenerate couldn't see them in Base.metadata and wanted to drop them on
# every check. The ORM models below mirror the migration exactly; they're
# non-TenantMixin because `organizations` *is* the tenant table and the rest
# are platform/metadata rows, not tenant-owned business data.


class Organization(Base):
    """A tenant. v1 ships with one row (LJM) seeded in migration 0016."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'standard'"))
    settings: Mapped[dict] = mapped_column(JSONType, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(26), ForeignKey("organizations.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'member'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="organization_members_tenant_user_key"),
        Index("ix_organization_members_tenant_id", "tenant_id"),
    )


class TenantCredential(Base):
    __tablename__ = "tenant_credentials"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(26), ForeignKey("organizations.id"), nullable=False)
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONType, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "connector", "kind", name="tenant_credentials_tenant_connector_kind_key"
        ),
        Index("ix_tenant_credentials_tenant_id", "tenant_id"),
    )


class TenantFeatureFlag(Base):
    __tablename__ = "tenant_feature_flags"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(26), ForeignKey("organizations.id"), nullable=False)
    flag: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict] = mapped_column(JSONType, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "flag", name="tenant_feature_flags_tenant_flag_key"),
        Index("ix_tenant_feature_flags_tenant_id", "tenant_id"),
    )


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, server_default=text("'{}'"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TenantSettings(Base):
    __tablename__ = "tenant_settings"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organizations.id"), nullable=False, unique=True
    )
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, server_default=text("'{}'"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SettingsRow(Base):
    __tablename__ = "settings"

    # Singleton row: id=1 enforced by app + a check constraint would be overkill for slice 1.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    threshold: Mapped[int] = mapped_column(Integer, default=70)
    auto_send_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_send_template_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("email_templates.id", ondelete="SET NULL")
    )
    tone: Mapped[str] = mapped_column(String(32), default="warm-professional")
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=50)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(128))
    # Auto-outreach (migration 0006). OFF by default. Compliance is mandatory:
    # a run with an empty `outreach_postal_address` (in Settings env) is refused.
    auto_outreach_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    auto_outreach_template_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("email_templates.id", ondelete="SET NULL")
    )
    auto_outreach_daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=20, server_default=text("20"))
    # Send-window in local hours [start, end). 8..18 = 08:00–18:00.
    auto_outreach_window_start_h: Mapped[int] = mapped_column(
        Integer, nullable=False, default=8, server_default=text("8")
    )
    auto_outreach_window_end_h: Mapped[int] = mapped_column(
        Integer, nullable=False, default=18, server_default=text("18")
    )
    # Which contact pipeline_status qualifies for auto-send. Default 'found' (never contacted yet).
    auto_outreach_status_filter: Mapped[str] = mapped_column(
        String(16), nullable=False, default="found", server_default=text("'found'")
    )
    # Fit-score weights blob (deterministic weights; see app/scoring/fit_score.py
    # for the DEFAULT_WEIGHTS shape). Ops can widen/narrow signals without a deploy.
    fit_weights: Mapped[dict | None] = mapped_column(JSONType)
    # Unsubscribe secret (migration 0007). Env `UNSUBSCRIBE_SECRET` wins when
    # present; otherwise this DB value is used. Backfilled once in the migration
    # and never returned via API.
    unsubscribe_secret: Mapped[str | None] = mapped_column(String(64))
    # Legacy (migration 0007), no longer read or written: the unsubscribe base
    # URL is a fixed value in `Settings.unsubscribe_base_url`. Column kept so
    # migration history stays intact.
    unsubscribe_base_url: Mapped[str | None] = mapped_column(String(500))
    # Minimum lead fit_score for auto-outreach (migration 0007). Contacts on
    # leads scoring below this are skipped; unscored leads are always skipped.
    auto_outreach_min_fit: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default=text("60"))
    # Auth JWT signing secret (migration 0008). Backfilled once at upgrade time
    # — same durable-secret-in-DB pattern as `unsubscribe_secret` in 0007. An
    # optional env override (`AUTH_JWT_SECRET`) wins at read time via
    # `effective_auth_jwt_secret` so ops can rotate without a DB write.
    auth_jwt_secret: Mapped[str | None] = mapped_column(String(128))
    # AI provider matrix (migration 0009). JSONB blob of
    # ``{feature: {provider, model}}``. See ``app.integrations.adapters.ai.provider.DEFAULT_FEATURES``
    # for the fallback shape — missing keys resolve against the code default.
    ai_features: Mapped[dict | None] = mapped_column(JSONType)
    # Mail connector mode override (migration 0013). Env > this > default.
    mail_sender_override: Mapped[str | None] = mapped_column(String(16))
    # Load-source verified timestamps (migration 0014).
    dat_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chr_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lb123_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    truckstop_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Brand settings for the rich email builder (migration 0020). All
    # nullable — code reads with a CLIENT-shaped fallback so existing rows
    # need no backfill.
    brand_company: Mapped[str | None] = mapped_column(String(128))
    brand_fleet: Mapped[str | None] = mapped_column(String(128))
    brand_dispatcher: Mapped[str | None] = mapped_column(String(128))
    brand_phone: Mapped[str | None] = mapped_column(String(64))
    brand_email: Mapped[str | None] = mapped_column(String(320))
    brand_logo_url: Mapped[str | None] = mapped_column(String(500))
    brand_accent_hex: Mapped[str | None] = mapped_column(String(7))


class User(TenantMixin, Base):
    """App login account (migration 0008).

    V1 is single-owner: migration 0008 seeds exactly one row
    (``owner@ljm-demo.local``) and no UI adds more. The ``role`` column stays
    for the seam when staff/Google-SSO lands later.

    ``password_hash`` is nullable so a future Google-SSO user — who has no
    local password — can live in the same table. Login refuses any row with
    ``is_active=False`` or ``password_hash IS NULL``.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)  # ULID
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="owner", server_default=text("'owner'"))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- Loads (migration 0010) -----------------------------------------
