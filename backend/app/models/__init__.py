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
    Numeric,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.orm import TenantMixin

# Prefer JSONB on Postgres; JSON fallback keeps the models importable elsewhere (docs, tools).
JSONType = JSONB().with_variant(JSON(), "sqlite")


class Lead(TenantMixin, Base):
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
    # Enrichment status (migration 0006). See app/pipeline/enrichment.py.
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_status: Mapped[str | None] = mapped_column(String(32))
    enrichment_error: Mapped[str | None] = mapped_column(Text)
    is_js_only_site: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    # Company discovery — LinkedIn company page + website URL discovered via Gemini
    # grounded search (never fetched). Evidence lives in `evidence` (citations).
    linkedin_company_url: Mapped[str | None] = mapped_column(String(500))
    website_url: Mapped[str | None] = mapped_column(String(500))
    # Fit score — deterministic computation in app/scoring/fit_score.py. Current
    # value on the row; every re-computation appended to `fit_score_history`.
    fit_score: Mapped[int | None] = mapped_column(Integer)
    fit_reasons: Mapped[list | None] = mapped_column(JSONType)
    fit_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Per-field provenance for the top-level broker/shipper contact strings
    # (migration 0010). Nullable; future enrichment runs fill these. The
    # per-named-contact source already lives on `lead_contacts.source`.
    primary_email_source: Mapped[str | None] = mapped_column(String(32))
    phone_source: Mapped[str | None] = mapped_column(String(32))
    address_source: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        # Partial-unique dedupe: MC → DOT → domain (plan rule).
        Index("leads_mc_key", "mc", unique=True, postgresql_where=(mc.isnot(None))),
        Index("leads_dot_key", "dot", unique=True, postgresql_where=(dot.isnot(None))),
        Index("leads_domain_key", "domain", unique=True, postgresql_where=(domain.isnot(None))),
        Index("leads_state_kind", "state", "kind"),
    )


class LeadSource(TenantMixin, Base):
    __tablename__ = "lead_sources"

    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)  # "FMCSA Census" / "Gemini Search"
    source_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)


class LeadContact(TenantMixin, Base):
    __tablename__ = "lead_contacts"

    # BigInteger on Postgres; Integer on SQLite so the rowid-alias autoincrement works in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    lead_id: Mapped[str] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(64))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Enrichment additions (migration 0006).
    source_url: Mapped[str | None] = mapped_column(String(500))
    is_decision_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    evidence: Mapped[dict | None] = mapped_column(JSONType)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[str | None] = mapped_column(String(16))
    pipeline_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="found", server_default=text("'found'")
    )
    pipeline_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("lead_contacts_lead_id", "lead_id"),
        Index("lead_contacts_lead_dm", "lead_id", "is_decision_maker"),
        Index("lead_contacts_lead_email", "lead_id", "email"),
        Index("lead_contacts_pipeline_status", "pipeline_status"),
    )


class LeadContactProvenance(TenantMixin, Base):
    """Per-sighting evidence for a `lead_contacts` row.

    One row per page-observation. Trust signal: N provenance rows for one contact
    means N distinct pages sighted the same email/phone — the "sighted 3 times"
    tooltip in the UI keys off this count.
    """

    __tablename__ = "lead_contact_provenance"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    contact_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        ForeignKey("lead_contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[dict | None] = mapped_column(JSONType)
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawl_runs.id", ondelete="SET NULL"))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("lead_contact_provenance_contact_disc", "contact_id", text("discovered_at DESC")),)


class EnrichmentCandidate(TenantMixin, Base):
    """Pre-promotion contacts for a `shipper_candidates` row.

    Copied into `lead_contacts` + `lead_contact_provenance` inside the promote
    transaction that creates/links the `leads` row.
    """

    __tablename__ = "enrichment_candidates"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    candidate_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("shipper_candidates.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    is_decision_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    confidence: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(500))
    evidence: Mapped[dict | None] = mapped_column(JSONType)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("enrichment_candidates_cand_dm", "candidate_id", "is_decision_maker"),)


class CrawlRun(TenantMixin, Base):
    __tablename__ = "crawl_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # queued / running / done / error
    kind: Mapped[str] = mapped_column(String(32))
    counts: Mapped[dict] = mapped_column(JSONType, default=dict)
    trigger: Mapped[str] = mapped_column(String(16))  # cron / on_demand
    error: Mapped[str | None] = mapped_column(Text)


class Score(TenantMixin, Base):
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


class EmailTemplate(TenantMixin, Base):
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
    # ``{feature: {provider, model}}``. See ``app.sources.provider.DEFAULT_FEATURES``
    # for the fallback shape — missing keys resolve against the code default.
    ai_features: Mapped[dict | None] = mapped_column(JSONType)
    # Mail connector mode override (migration 0013). Env > this > default.
    mail_sender_override: Mapped[str | None] = mapped_column(String(16))
    # Load-source verified timestamps (migration 0014).
    dat_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chr_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lb123_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    truckstop_configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class ShipperCandidate(TenantMixin, Base):
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

    # Enrichment additions (migration 0006). Same shape as on `leads`.
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_status: Mapped[str | None] = mapped_column(String(32))
    enrichment_error: Mapped[str | None] = mapped_column(Text)
    is_js_only_site: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    linkedin_company_url: Mapped[str | None] = mapped_column(String(500))
    website_url: Mapped[str | None] = mapped_column(String(500))
    fit_score: Mapped[int | None] = mapped_column(Integer)
    fit_reasons: Mapped[list | None] = mapped_column(JSONType)
    fit_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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


class FitScoreHistory(TenantMixin, Base):
    """Append-only history of every fit-score computation. Save everything."""

    __tablename__ = "fit_score_history"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    lead_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("leads.id", ondelete="CASCADE"))
    candidate_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("shipper_candidates.id", ondelete="CASCADE")
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    signals: Mapped[dict] = mapped_column(JSONType, default=dict)
    weights: Mapped[dict] = mapped_column(JSONType, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class Load(TenantMixin, Base):
    """A broker-posted lane snapshot ingested from a load source.

    Dedupe key: ``(source, source_ref)`` — vendor row id tells us 'same load'
    across refreshes. Added-row counters compare the batch's keys against the
    seen-today set.
    """

    __tablename__ = "loads"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    broker_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default=text("''"))
    broker_email: Mapped[str | None] = mapped_column(String(320))
    broker_phone: Mapped[str | None] = mapped_column(String(64))
    origin_city: Mapped[str | None] = mapped_column(String(128))
    origin_state: Mapped[str | None] = mapped_column(String(8))
    dest_city: Mapped[str | None] = mapped_column(String(128))
    dest_state: Mapped[str | None] = mapped_column(String(8))
    pickup_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    equipment: Mapped[str | None] = mapped_column(String(64))
    rate_usd: Mapped[float | None] = mapped_column(Numeric(10, 2))
    miles: Mapped[int | None] = mapped_column(Integer)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw: Mapped[dict | None] = mapped_column(JSONType)

    __table_args__ = (
        Index("loads_source_ref_uq", "source", "source_ref", unique=True),
        Index("loads_pickup_idx", "pickup_date"),
    )


# ---------- Mail (migration 0011/0012) -------------------------------------


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
