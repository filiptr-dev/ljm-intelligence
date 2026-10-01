"""analysis models — AI usage log + predictions + further-analysis storage.

Re-exported via `app.models` so Alembic autogenerate still sees the full
`Base.metadata`. New columns/indexes land here, not in the re-export.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class AiUsageLog(TenantMixin, Base):
    """One row per adapter call (migration 0009)."""

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


# ---- Prediction tables (migration 0019) ----------------------------------


class PredictionRun(TenantMixin, Base):
    """One row per nightly analysis run — ops visibility on when + how long."""

    __tablename__ = "prediction_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default=text("'running'"))
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))

    __table_args__ = (
        Index("ix_prediction_runs_tenant", "tenant_id", "kind", "started_at"),
    )


class BrokerPrediction(TenantMixin, Base):
    """One row per broker per nightly run."""

    __tablename__ = "broker_predictions"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    broker_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    broker_name: Mapped[str | None] = mapped_column(String(255))
    win_probability: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    health_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default=text("50"))
    best_send_hour: Mapped[int | None] = mapped_column(Integer)
    is_slow_payer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    churn_risk: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    reply_speed_lift: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default=text("1"))
    first_touch_latency_days: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_broker_predictions_tenant", "tenant_id", "broker_domain", "computed_at"),
    )


class LanePrediction(TenantMixin, Base):
    __tablename__ = "lane_predictions"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    origin: Mapped[str] = mapped_column(String(128), nullable=False)
    dest: Mapped[str] = mapped_column(String(128), nullable=False)
    equipment: Mapped[str | None] = mapped_column(String(64))
    price_p50: Mapped[float | None] = mapped_column(Numeric(10, 2))
    price_p75: Mapped[float | None] = mapped_column(Numeric(10, 2))
    price_p90: Mapped[float | None] = mapped_column(Numeric(10, 2))
    season_hint: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_lane_predictions_tenant", "tenant_id", "origin", "dest"),
    )


class BrokerLookalike(TenantMixin, Base):
    __tablename__ = "broker_lookalikes"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    broker_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    peer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_broker_lookalikes_tenant", "tenant_id", "broker_domain"),
        UniqueConstraint("tenant_id", "broker_domain", "peer_domain", name="uq_broker_lookalike"),
    )


class ObjectionCluster(TenantMixin, Base):
    """Per-broker objection clustering — labelled bucket + count + exemplar."""

    __tablename__ = "objection_clusters"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    broker_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    exemplar: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_objection_clusters_tenant", "tenant_id", "broker_domain"),
    )


class ForgetContactAudit(TenantMixin, Base):
    """Audit row written when the owner purges an address from the inbox."""

    __tablename__ = "forget_contact_audit"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    messages_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    insights_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    performed_by: Mapped[str | None] = mapped_column(String(255))
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_forget_contact_audit_tenant", "tenant_id", "performed_at"),
    )
