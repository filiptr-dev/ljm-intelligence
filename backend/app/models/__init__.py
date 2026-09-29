"""All ORM models — imported here so Alembic autogenerate + `Base.metadata` see them.

Slice 1: define the schema per the plan so the initial migration ships the whole shape.
Later slices only add columns / indexes, never restructure.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Prefer JSONB on Postgres; JSON fallback keeps the models importable elsewhere (docs, tools).
JSONType = JSONB().with_variant(JSON(), "sqlite")


class Lead(Base):
    __tablename__ = "leads"

    # id shape "MC-…" / "DOT-…" / "DOMAIN-…" — see the plan's cross-service Lead type.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mc: Mapped[str | None] = mapped_column(String(32))
    dot: Mapped[str | None] = mapped_column(String(32))
    domain: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))  # Broker / Shipper / Forwarder
    state: Mapped[str] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    primary_email: Mapped[str | None] = mapped_column(String(255))
    current_score: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_seen_run_id: Mapped[str | None] = mapped_column(String(64))
    last_seen_run_id: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)
    evidence: Mapped[dict] = mapped_column(JSONType, default=dict)
    recommendations: Mapped[list] = mapped_column(JSONType, default=list)

    __table_args__ = (
        # Partial-unique dedupe: MC → DOT → domain (plan rule).
        Index("leads_mc_key", "mc", unique=True, postgresql_where=(mc.isnot(None))),
        Index("leads_dot_key", "dot", unique=True, postgresql_where=(dot.isnot(None))),
        Index("leads_domain_key", "domain", unique=True, postgresql_where=(domain.isnot(None))),
        Index("leads_state_kind", "state", "kind"),
    )


class LeadSource(Base):
    __tablename__ = "lead_sources"

    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)  # "FMCSA Census" / "Gemini Search"
    source_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)


class LeadContact(Base):
    __tablename__ = "lead_contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(64))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("lead_contacts_lead_id", "lead_id"),)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # queued / running / done / error
    kind: Mapped[str] = mapped_column(String(32))
    counts: Mapped[dict] = mapped_column(JSONType, default=dict)
    trigger: Mapped[str] = mapped_column(String(16))  # cron / on_demand
    error: Mapped[str | None] = mapped_column(Text)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("crawl_runs.id", ondelete="CASCADE"))
    score: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[dict] = mapped_column(JSONType, default=dict)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("scores_lead_id_created_at", "lead_id", "created_at"),)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    tokens: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class SentLog(Base):
    __tablename__ = "sent_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawl_runs.id", ondelete="SET NULL"))
    template_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("email_templates.id", ondelete="SET NULL")
    )
    mode: Mapped[str] = mapped_column(String(16))  # simulated / real
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Suppression(Base):
    __tablename__ = "suppression"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapacityPost(Base):
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


class CallOutcome(Base):
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
    lead_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    # Only meaningful when outcome='callback'; Date (no time) — we schedule by day.
    callback_at: Mapped[date | None] = mapped_column(Date(), nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
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
