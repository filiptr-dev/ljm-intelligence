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
    Float,
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
    template_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("email_templates.id", ondelete="SET NULL"))
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


class ShipperCandidate(Base):
    """Un-promoted discovery bucket for OSM finds + FMCSA shippers.

    The `/tools/shipper-finder` tool reads from here; only "Add to leads" writes
    into `leads`. **One row per company** (Slice 2a — merged rows): the `sources`
    JSONB list tells you which public datasets confirmed it, and per-source
    evidence sits in the `evidence` JSON. `fmcsa_dot` / `fmcsa_mc` / `osm_ref`
    are the source-key columns the merge fn dedupes against (partial-UNIQUE
    WHERE NOT NULL — mirrors leads' MC/DOT/domain pattern).

    `mc` and `dot` are retained as denormalised copies of `fmcsa_mc`/`fmcsa_dot`
    so the ranker's authority-label path doesn't have to know about the merge
    (see the amendment note in the plan). The merge fn keeps them in sync.

    See migrations 0004 (initial shape) + 0005 (this merged reshape).
    """

    __tablename__ = "shipper_candidates"

    # Neutral id (26-char ULID; String(32) for headroom). No source prefix — the
    # row represents a company, not a source. That was the 0004 mistake.
    id: Mapped[str] = mapped_column(String(32), primary_key=True)

    # Source-key columns — each nullable, each partial-UNIQUE WHERE NOT NULL so a
    # repeat sighting lands on the existing row instead of duplicating.
    fmcsa_dot: Mapped[str | None] = mapped_column(String(32))
    fmcsa_mc: Mapped[str | None] = mapped_column(String(32))
    osm_ref: Mapped[str | None] = mapped_column(String(64))  # 'way/123' | 'node/7' | 'relation/9'

    # Which public datasets confirmed this row. JSONB list on Postgres, JSON on
    # SQLite. Kept as a list rather than text[] for portability.
    sources: Mapped[list] = mapped_column(JSONType, nullable=False, default=list, server_default=text("'[]'"))
    # Short human-readable "why merged"; null on single-source rows.
    match_reason: Mapped[str | None] = mapped_column(String(255))

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    city: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float())
    lng: Mapped[float | None] = mapped_column(Float())
    # Denormalised copies of fmcsa_mc / fmcsa_dot — kept for ranker/serializer
    # ergonomics; merge fn writes both.
    mc: Mapped[str | None] = mapped_column(String(32))
    dot: Mapped[str | None] = mapped_column(String(32))
    domain: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    primary_email: Mapped[str | None] = mapped_column(String(255))
    osm_tags: Mapped[dict | None] = mapped_column(JSONType)
    raw: Mapped[dict | None] = mapped_column(JSONType)
    # `evidence` layout: {"fmcsa": {...}, "osm": {...}, "match": {"rule": "...", "confidence": "..."}}
    evidence: Mapped[dict | None] = mapped_column(JSONType)
    # No hard FK to leads.id — un-promoted rows have no lead yet. The /promote
    # endpoint sets this in the same transaction that inserts the lead.
    promoted_lead_id: Mapped[str | None] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("shipper_candidates_state", "state"),
        # Partial index mirrors migration 0004 — only un-promoted rows.
        Index(
            "shipper_candidates_unpromoted",
            "promoted_lead_id",
            postgresql_where=(promoted_lead_id.is_(None)),
        ),
        # Partial-UNIQUE dedupe on the source-key columns — mirrors migration 0005.
        Index(
            "shipper_candidates_fmcsa_dot_key",
            "fmcsa_dot",
            unique=True,
            postgresql_where=(fmcsa_dot.isnot(None)),
        ),
        Index(
            "shipper_candidates_fmcsa_mc_key",
            "fmcsa_mc",
            unique=True,
            postgresql_where=(fmcsa_mc.isnot(None)),
        ),
        Index(
            "shipper_candidates_osm_ref_key",
            "osm_ref",
            unique=True,
            postgresql_where=(osm_ref.isnot(None)),
        ),
    )
