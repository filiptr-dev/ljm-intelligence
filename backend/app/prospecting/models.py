"""prospecting models — SQLAlchemy tables rehomed from `app/models/__init__.py`.

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
    # Main lane — restored by migration 0021 for the broker detail "Main lane"
    # card. All nullable; the UI renders the card only when at least one is
    # set, so pre-migration rows stay silent (not faked).
    lane_origin_region: Mapped[str | None] = mapped_column(String(64))
    lane_destination_region: Mapped[str | None] = mapped_column(String(64))
    lane_miles_band: Mapped[str | None] = mapped_column(String(32))
    lane_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    # Take-it flow (migration 0024). Status is the user-visible lifecycle;
    # ``broker_lead_id`` links the row back to a known broker when we have
    # one (same lane posted on dat + inbox collapses to one row and the FK
    # is attached from the inbox side, which carries the broker email/MC).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new", server_default=text("'new'")
    )
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broker_lead_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("leads.id", ondelete="SET NULL")
    )
    # Deterministic sha256[:32] of the normalised (broker|o_state|d_state|
    # pickup::date|equipment) tuple — same inputs → same hash → group URL
    # survives a re-ingest. Backfilled by migration 0024.
    dedupe_group_hash: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", server_default=text("''")
    )

    __table_args__ = (
        Index("loads_source_ref_uq", "source", "source_ref", unique=True),
        Index("loads_pickup_idx", "pickup_date"),
        Index("loads_dedupe_group_idx", "dedupe_group_hash"),
        Index("loads_broker_lead_idx", "broker_lead_id"),
    )


class AgentRun(TenantMixin, Base):
    """Headless-agent run log — one row per run.

    Written by ``agent_browser.driver.AgentSource.fetch`` on every attempt
    (success *and* failure). The daily cap is a ``COUNT(*)`` filtered by
    ``source`` + ``started_at::date = today``; we never need to delete, the
    history is cheap and small.
    """

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ok", server_default=text("'ok'")
    )
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("agent_runs_source_started_idx", "source", "started_at"),
        Index("agent_runs_tenant_started_idx", "tenant_id", "started_at"),
    )


# ---------- Mail (migration 0011/0012) -------------------------------------
