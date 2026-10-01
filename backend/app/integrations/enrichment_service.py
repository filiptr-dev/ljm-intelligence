"""Enrichment service — on-demand enrich + metrics + unsubscribe-token verify.

Thin business layer behind ``app/api/enrichment.py``. The heavy pipeline
(web fetch, Gemini extraction, DB writes) still lives in
``app.pipeline.enrichment``; this module hoists the DB-reading projections
(``_load_lead_view``, candidate-detail assembly, metrics aggregation) and
the unsubscribe-token verification out of the router.

Returns plain dataclasses; raises :class:`NotFoundError` for 404-worthy
situations so the router keeps the HTTP mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._auth import verify_unsubscribe_token
from app.models import (
    EnrichmentCandidate,
    Lead,
    LeadContact,
    SettingsRow,
    ShipperCandidate,
    Suppression,
)
from app.pipeline.enrichment import enrich_company
from app.services.unsub_config import effective_secret


class NotFoundError(Exception):
    """Raised when a target (lead / candidate / contact) does not exist."""


class UnsubscribeConfigError(Exception):
    """Raised when the unsubscribe secret is missing — token can never verify."""


class InvalidUnsubscribeTokenError(Exception):
    """Raised when a presented unsubscribe token fails HMAC verification."""


# ---------- dataclasses -----------------------------------------------------


@dataclass
class DecisionMakerRow:
    id: int
    name: str | None
    title: str | None
    linkedin_url: str | None
    email: str | None
    phone: str | None
    confidence: str | None
    source: str | None
    source_url: str | None
    evidence: dict | None
    last_verified_at: str | None
    pipeline_status: str


@dataclass
class WebsiteContactRow:
    id: int
    name: str | None
    title: str | None
    email: str | None
    phone: str | None
    confidence: str | None
    source: str | None
    source_url: str | None
    last_verified_at: str | None
    pipeline_status: str


@dataclass
class EnrichmentResultRow:
    lead_id: str | None
    candidate_id: str | None
    status: str
    error: str | None
    is_js_only_site: bool
    decision_makers: list[DecisionMakerRow]
    contacts: list[WebsiteContactRow]
    pages_fetched: int
    linkedin_company_url: str | None
    website_url: str | None
    ran_at: str
    per_stage: dict[str, str] = field(default_factory=dict)
    fit_score: int | None = None
    fit_reasons: list[str] = field(default_factory=list)


@dataclass
class MetricsResult:
    scope: str  # "shipper" | "broker"
    window_days: int
    enriched: int
    reachable: int
    contacted: int
    replied: int
    enriched_all_time: int
    reachable_all_time: int
    reachable_rate: float
    replied_rate: float


@dataclass
class UnsubscribeResult:
    ok: bool
    email: str | None
    already: bool


# ---------- helpers ---------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def load_lead_view(session: AsyncSession, lead_id: str) -> EnrichmentResultRow:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFoundError(f"lead not found: {lead_id}")
    contacts = (
        (
            await session.execute(
                select(LeadContact).where(LeadContact.lead_id == lead_id).order_by(LeadContact.id)
            )
        )
        .scalars()
        .all()
    )
    dms = [c for c in contacts if c.is_decision_maker]
    others = [c for c in contacts if not c.is_decision_maker]
    return EnrichmentResultRow(
        lead_id=lead.id,
        candidate_id=None,
        status=lead.enrichment_status or "skipped",
        error=lead.enrichment_error,
        is_js_only_site=bool(lead.is_js_only_site),
        decision_makers=[
            DecisionMakerRow(
                id=c.id,
                name=c.name,
                title=c.title,
                linkedin_url=c.linkedin_url,
                email=c.email,
                phone=c.phone,
                confidence=c.confidence,
                source=c.source,
                source_url=c.source_url,
                evidence=c.evidence,
                last_verified_at=_iso(c.last_verified_at),
                pipeline_status=c.pipeline_status,
            )
            for c in dms
        ],
        contacts=[
            WebsiteContactRow(
                id=c.id,
                name=c.name,
                title=c.title,
                email=c.email,
                phone=c.phone,
                confidence=c.confidence,
                source=c.source,
                source_url=c.source_url,
                last_verified_at=_iso(c.last_verified_at),
                pipeline_status=c.pipeline_status,
            )
            for c in others
        ],
        pages_fetched=0,
        linkedin_company_url=lead.linkedin_company_url,
        website_url=lead.website_url,
        ran_at=_iso(lead.last_enriched_at) or datetime.now(UTC).isoformat(),
        fit_score=lead.fit_score,
        fit_reasons=list(lead.fit_reasons or []),
    )


async def enrich_lead(
    sessionmaker: Any, settings: Any, lead_id: str, *, force: bool
) -> EnrichmentResultRow:
    async with sessionmaker() as s:
        async with s.begin():
            result = await enrich_company(s, settings, lead_id=lead_id, force=force)
        return await load_lead_view(s, result.lead_id or lead_id)


async def enrich_candidate(
    sessionmaker: Any, settings: Any, candidate_id: str, *, force: bool
) -> EnrichmentResultRow:
    async with sessionmaker() as s:
        async with s.begin():
            result = await enrich_company(s, settings, candidate_id=candidate_id, force=force)
        cand = (
            await s.execute(select(ShipperCandidate).where(ShipperCandidate.id == candidate_id))
        ).scalar_one_or_none()
        if cand is None:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        rows = (
            (
                await s.execute(
                    select(EnrichmentCandidate)
                    .where(EnrichmentCandidate.candidate_id == candidate_id)
                    .order_by(EnrichmentCandidate.id)
                )
            )
            .scalars()
            .all()
        )
        return EnrichmentResultRow(
            lead_id=None,
            candidate_id=candidate_id,
            status=cand.enrichment_status or result.status,
            error=cand.enrichment_error,
            is_js_only_site=bool(cand.is_js_only_site),
            decision_makers=[
                DecisionMakerRow(
                    id=r.id,
                    name=r.name,
                    title=r.title,
                    linkedin_url=r.linkedin_url,
                    email=r.email,
                    phone=r.phone,
                    confidence=r.confidence,
                    source=r.source,
                    source_url=r.source_url,
                    evidence=r.evidence,
                    last_verified_at=None,
                    pipeline_status="found",
                )
                for r in rows
                if r.is_decision_maker
            ],
            contacts=[
                WebsiteContactRow(
                    id=r.id,
                    name=r.name,
                    title=r.title,
                    email=r.email,
                    phone=r.phone,
                    confidence=r.confidence,
                    source=r.source,
                    source_url=r.source_url,
                    last_verified_at=None,
                    pipeline_status="found",
                )
                for r in rows
                if not r.is_decision_maker
            ],
            pages_fetched=result.pages_fetched,
            linkedin_company_url=cand.linkedin_company_url,
            website_url=cand.website_url,
            ran_at=_iso(cand.last_enriched_at) or datetime.now(UTC).isoformat(),
            fit_score=cand.fit_score,
            fit_reasons=list(cand.fit_reasons or []),
        )


async def metrics(session: AsyncSession, kind: str) -> MetricsResult:
    kind_val = "Shipper" if kind == "shipper" else "Broker"
    window_days = 30
    leads = (await session.execute(select(Lead).where(Lead.kind == kind_val))).scalars().all()
    lead_ids = [lead.id for lead in leads]
    if not lead_ids:
        return MetricsResult(
            scope=kind,
            window_days=window_days,
            enriched=0,
            reachable=0,
            contacted=0,
            replied=0,
            enriched_all_time=0,
            reachable_all_time=0,
            reachable_rate=0.0,
            replied_rate=0.0,
        )
    contacts = (
        await session.execute(select(LeadContact).where(LeadContact.lead_id.in_(lead_ids)))
    ).scalars().all()
    by_lead_has_contact: dict[str, bool] = {}
    by_lead_reachable: dict[str, bool] = {}
    by_lead_contacted: dict[str, bool] = {}
    by_lead_replied: dict[str, bool] = {}
    for c in contacts:
        by_lead_has_contact[c.lead_id] = True
        if c.email or c.phone:
            by_lead_reachable[c.lead_id] = True
        if c.pipeline_status in ("contacted", "replied", "won", "lost"):
            by_lead_contacted[c.lead_id] = True
        if c.pipeline_status in ("replied", "won"):
            by_lead_replied[c.lead_id] = True

    enriched_all = sum(1 for _ in by_lead_has_contact)
    reachable_all = sum(1 for _ in by_lead_reachable)
    contacted = sum(1 for _ in by_lead_contacted)
    replied = sum(1 for _ in by_lead_replied)
    cutoff = datetime.now(UTC).timestamp() - window_days * 86400
    recent_lead_ids: set[str] = set()
    for lead in leads:
        if lead.last_enriched_at is not None and lead.last_enriched_at.timestamp() >= cutoff:
            recent_lead_ids.add(lead.id)
    enriched = sum(1 for lid in by_lead_has_contact if lid in recent_lead_ids) or enriched_all
    reachable = sum(1 for lid in by_lead_reachable if lid in recent_lead_ids) or reachable_all

    reachable_rate = (reachable / enriched) if enriched else 0.0
    replied_rate = (replied / contacted) if contacted else 0.0
    return MetricsResult(
        scope=kind,
        window_days=window_days,
        enriched=enriched,
        reachable=reachable,
        contacted=contacted,
        replied=replied,
        enriched_all_time=enriched_all,
        reachable_all_time=reachable_all,
        reachable_rate=round(reachable_rate, 4),
        replied_rate=round(replied_rate, 4),
    )


# ---------- unsubscribe -----------------------------------------------------


async def verify_token(sessionmaker: Any, settings: Any, token: str) -> int:
    """Verify an unsub token, returning the ``LeadContact.id`` it points at.

    Raises :class:`UnsubscribeConfigError` when no secret is configured
    (misconfigured deploy must never succeed at mass-unsubscribing) or
    :class:`InvalidUnsubscribeTokenError` on a bad / forged token.
    """
    async with sessionmaker() as s:
        cfg = (
            await s.execute(select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
    secret = effective_secret(settings, cfg)
    if not secret:
        raise UnsubscribeConfigError("unsubscribe not configured")
    cid = verify_unsubscribe_token(token, secret)
    if cid is None:
        raise InvalidUnsubscribeTokenError("invalid unsubscribe token")
    return cid


async def apply_unsubscribe(sessionmaker: Any, contact_id: int) -> UnsubscribeResult:
    """Suppress the contact's email and mark the contact ``lost``."""
    async with sessionmaker() as s:
        contact = (
            await s.execute(select(LeadContact).where(LeadContact.id == contact_id))
        ).scalar_one_or_none()
        if contact is None:
            raise NotFoundError("contact not found")
        email = (contact.email or "").lower() or None
        already = False
        if email:
            existing = (
                await s.execute(select(Suppression).where(Suppression.email == email))
            ).scalar_one_or_none()
            if existing is None:
                s.add(Suppression(email=email, reason="do_not_contact"))
            else:
                already = True
        if contact.pipeline_status != "lost":
            contact.pipeline_status = "lost"
            contact.pipeline_status_at = datetime.now(UTC)
        await s.commit()
    return UnsubscribeResult(ok=True, email=email, already=already)
