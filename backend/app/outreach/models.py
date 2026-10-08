"""outreach models — SQLAlchemy tables rehomed from `app/models/__init__.py`.

Re-exported via `app.models` so Alembic autogenerate still sees the full
`Base.metadata`. New columns/indexes land here, not in the re-export.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.orm import JSONType, TenantMixin


class EmailTemplate(TenantMixin, Base):
    __tablename__ = "email_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    tokens: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SentLog(TenantMixin, Base):
    __tablename__ = "sent_log"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawl_runs.id", ondelete="SET NULL"))
    template_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("email_templates.id", ondelete="SET NULL"))
    mode: Mapped[str] = mapped_column(String(16))  # simulated / real
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Enrichment (migration 0006): FK the FIND→CONTACT→INBOX loop keys off.
    contact_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("lead_contacts.id", ondelete="SET NULL"),
    )
    # Gmail provider fields (migration 0013). Nullable so pre-provider rows read unchanged.
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    thread_id: Mapped[str | None] = mapped_column(String(255))
    in_reply_to: Mapped[str | None] = mapped_column(String(255))
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    __table_args__ = (
        Index("sent_log_contact_sent", "contact_id", text("sent_at DESC")),
        Index("sent_log_thread_idx", "thread_id"),
    )


class Suppression(TenantMixin, Base):
    __tablename__ = "suppression"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapacityPost(TenantMixin, Base):
    """Two-post-type table:
    kind='truck'  → equipment + origin (city/state) + available_date + destinations[]
    kind='load'   → equipment + origin + destination + pickup_date + weight + rate

    Additive to the baseline schema — never restructures.
    """

    __tablename__ = "capacity_posts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # 'truck' | 'load'
    equipment: Mapped[str] = mapped_column(String(64))
    origin_city: Mapped[str | None] = mapped_column(String(128))
    origin_state: Mapped[str] = mapped_column(String(2))
    origin_lat: Mapped[float | None] = mapped_column()
    origin_lng: Mapped[float | None] = mapped_column()
    # truck: preferred destinations as list of state codes
    destinations: Mapped[list] = mapped_column(JSONType, default=list)
    # truck-only
    available_date: Mapped[str | None] = mapped_column(String(32))
    # load-only
    dest_city: Mapped[str | None] = mapped_column(String(128))
    dest_state: Mapped[str | None] = mapped_column(String(2))
    pickup_date: Mapped[str | None] = mapped_column(String(32))
    weight_lbs: Mapped[int | None] = mapped_column(Integer)
    rate_usd: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open / matched / closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("capacity_posts_kind_status", "kind", "status"),)


class CallOutcome(TenantMixin, Base):
    """One row per phone-call decision on a Lead.

    Outcomes are events, not state, so we get a full timeline per lead:
        booked · callback · not_interested · no_answer

    Additive to the baseline schema — never restructures shipped tables. Migration 0003.
    """

    __tablename__ = "call_outcomes"

    # BigInteger on Postgres (matches migration 0003); Integer on SQLite so the
    # rowid-alias autoincrement works in tests. Zero effect on Neon.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    # Only meaningful when outcome='callback'; Date (no time) — we schedule by day.
    callback_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    logged_by: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('booked','callback','not_interested','no_answer')",
            name="call_outcomes_outcome_check",
        ),
        # Mirror migration 0003 exactly: (lead_id, logged_at DESC) — the hot lookup for
        # "latest outcome per lead" wants the newest first. Without DESC the ORM would drift
        # from the migration, and Alembic autogenerate would want to "correct" the DB.
        Index("call_outcomes_lead_id_logged_at", "lead_id", text("logged_at DESC")),
        Index(
            "call_outcomes_callback_at",
            "callback_at",
            postgresql_where=(outcome == "callback"),
        ),
    )
