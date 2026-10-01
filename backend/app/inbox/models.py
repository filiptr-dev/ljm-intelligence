"""inbox models — SQLAlchemy tables rehomed from `app/models/__init__.py`.

Re-exported via `app.models` so Alembic autogenerate still sees the full
`Base.metadata`. New columns/indexes land here, not in the re-export.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.orm import JSONType, TenantMixin


class MailMessage(TenantMixin, Base):
    __tablename__ = "mail_messages"

    mailbox: Mapped[str] = mapped_column(String(255), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    history_id: Mapped[str] = mapped_column(String(64), nullable=False)
    from_addr: Mapped[str] = mapped_column(String(320), nullable=False, default="", server_default=text("''"))
    # Normalised, indexed from_email (0016 added column + composite index; 0018 adds a
    # plain index). Stamped by ingest on every new row.
    email_lower: Mapped[str | None] = mapped_column(String(320))
    to_addrs: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    cc_addrs: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(String(255))
    references_hdr: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    labels: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Retention — ingest stamps now()+18mo. Sweep job (next pass) deletes past rows.
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    __table_args__ = (
        Index("mail_messages_thread_idx", "thread_id"),
        Index("mail_messages_from_idx", "from_addr"),
        Index("mail_messages_sent_at_idx", "sent_at"),
        Index("mail_messages_mbx_hist_idx", "mailbox", "history_id"),
    )


class MailCursor(TenantMixin, Base):
    __tablename__ = "mail_cursors"

    mailbox: Mapped[str] = mapped_column(String(255), primary_key=True)
    history_id: Mapped[str] = mapped_column(String(64), nullable=False)
    backfilled_through_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---- Inbox analysis tables (migration 0018) --------------------------------


class MessageInsight(TenantMixin, Base):
    """One analysis row per (message, model_version).

    The triage call writes intent + urgency + sentiment + confidence in a single
    pass. ``raw["demo"]`` labels on the simulated corpus are used verbatim when
    the AI provider is the Null/stub adapter, so tests never hit Gemini.
    """

    __tablename__ = "message_insights"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    from_email_normalized: Mapped[str] = mapped_column(
        String(320), nullable=False, default="", server_default=text("''")
    )
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", server_default=text("'normal'"))
    sentiment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    rate_usd: Mapped[float | None] = mapped_column(Numeric(10, 2))
    lane_from: Mapped[str | None] = mapped_column(String(128))
    lane_to: Mapped[str | None] = mapped_column(String(128))
    equipment: Mapped[str | None] = mapped_column(String(64))
    broker_name: Mapped[str | None] = mapped_column(String(255))
    evidence: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="demo", server_default=text("'demo'"))
    model_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="demo-v1", server_default=text("'demo-v1'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_message_insights_tenant", "tenant_id"),
        Index("ix_message_insights_thread", "tenant_id", "thread_id"),
        Index("ix_message_insights_intent", "tenant_id", "intent"),
        Index("ix_message_insights_from", "tenant_id", "from_email_normalized"),
        UniqueConstraint(
            "tenant_id", "mailbox", "message_id", "model_version",
            name="uq_message_insights_msg_model",
        ),
    )


class NoReplyTracker(TenantMixin, Base):
    """One row per outbound-last thread still waiting on a reply.

    Deleted when an inbound message lands on the same (mailbox, thread_id).
    ``days_waiting`` is derived in the query (not stored) so it stays live
    without a nightly refresh.
    """

    __tablename__ = "no_reply_tracker"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    to_email_normalized: Mapped[str] = mapped_column(
        String(320), nullable=False, default="", server_default=text("''")
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    we_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    follow_up_suggested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_no_reply_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "mailbox", "thread_id", name="uq_no_reply_thread"),
    )
