"""analysis models — SQLAlchemy tables rehomed from `app/models/__init__.py`.

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


class AiUsageLog(TenantMixin, Base):
    """One row per adapter call (migration 0009).

    Prompt bodies are NEVER written here; only ``input_hash`` (sha256) for
    coarse analytics. Rows under a shared ``compare_id`` form a side-by-side
    comparison record for the Settings → AI Compare view.
    """

    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    feature: Mapped[str] = mapped_column(String(48), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    cost_usd: Mapped[Numeric] = mapped_column(Numeric(10, 6), nullable=False, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    compare_id: Mapped[str | None] = mapped_column(String(32))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ai_usage_log_feature_created", "feature", "created_at"),
        Index("ai_usage_log_provider_created", "provider", "created_at"),
        Index("ai_usage_log_compare_id", "compare_id"),
    )
