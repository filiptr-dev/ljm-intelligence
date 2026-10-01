"""inbox models — SQLAlchemy tables rehomed from `app/models/__init__.py`.

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
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    false,
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
