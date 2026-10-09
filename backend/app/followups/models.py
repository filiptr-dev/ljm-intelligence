"""ORM for the one table this module owns: ``followup_notes``.

One row per (tenant, lead). Notes here are a dispatcher's running thought
— history already lives in ``sent_log`` / ``call_outcomes`` / ``mail_messages``.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.orm import TenantMixin


class FollowupNote(TenantMixin, Base):
    __tablename__ = "followup_notes"

    tenant_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    lead_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    next_touch: Mapped[date | None] = mapped_column(Date(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("followup_notes_next_touch_idx", "tenant_id", "next_touch"),)
