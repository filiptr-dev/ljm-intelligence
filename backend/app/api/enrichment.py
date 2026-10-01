"""Enrichment routes — on-demand + metrics + auto-outreach + unsubscribe.

Kept in one module so the router prefix is uniform (`/enrichment`) and the
frontend `lib/api/enrichment.ts` maps 1:1 to a single OpenAPI tag.

Every route ships an explicit `response_model=…` so `openapi-typescript`
generates readable types for the frontend typed client (locked convention
from the shipper-finder plan gate Q4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api._auth import check_secret, verify_unsubscribe_token
from app.config import Settings
from app.models import (
    EnrichmentCandidate,
    Lead,
    LeadContact,
    SentLog,
    SettingsRow,
    ShipperCandidate,
    Suppression,
)
from app.pipeline.enrichment import (
    enrich_company,
    mark_contact_contacted,
)
from app.services.unsub_config import (
    build_unsub_link,
    effective_secret,
    effective_unsub,
    unsub_headers,
    with_unsub_footer,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/enrichment", tags=["enrichment"])


# ---------- shapes ----------------------------------------------------------


class DecisionMakerOut(BaseModel):
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


class WebsiteContactOut(BaseModel):
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


class EnrichmentResultOut(BaseModel):
    lead_id: str | None
    candidate_id: str | None
    status: str
    error: str | None
    is_js_only_site: bool
    decision_makers: list[DecisionMakerOut]
    contacts: list[WebsiteContactOut]
    pages_fetched: int
    linkedin_company_url: str | None
    website_url: str | None
    ran_at: str
    per_stage: dict[str, str] = Field(default_factory=dict)
    fit_score: int | None = None
    fit_reasons: list[str] = Field(default_factory=list)


class EnrichmentMetricsOut(BaseModel):
    scope: Literal["shipper", "broker"]
    window_days: int
    enriched: int
    reachable: int
    contacted: int
    replied: int
    enriched_all_time: int
    reachable_all_time: int
    reachable_rate: float
    replied_rate: float


AutoSendStatus = Literal[
    "ok",
    "disabled",
    "no_footer",
    "no_template",
    "no_unsub_config",
    "outside_window",
]


class AutoSendOut(BaseModel):
    # Narrow literal so openapi-typescript emits a union, giving the frontend
    # exhaustive switch coverage instead of an opaque `string`.
    status: AutoSendStatus
    sent: int
    skipped_suppressed: int
    skipped_cap: int
    dry_run: bool


# ---------- helpers ---------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def _load_lead_view(session, lead_id: str) -> EnrichmentResultOut:
    lead = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise HTTPException(404, "lead not found")
    contacts = (
        (await session.execute(select(LeadContact).where(LeadContact.lead_id == lead_id).order_by(LeadContact.id)))
        .scalars()
        .all()
    )
    dms = [c for c in contacts if c.is_decision_maker]
    others = [c for c in contacts if not c.is_decision_maker]
    return EnrichmentResultOut(
        lead_id=lead.id,
        candidate_id=None,
        status=lead.enrichment_status or "skipped",
        error=lead.enrichment_error,
        is_js_only_site=bool(lead.is_js_only_site),
        decision_makers=[
            DecisionMakerOut(
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
            WebsiteContactOut(
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


# ---------- on-demand routes -----------------------------------------------


@router.post("/leads/{lead_id}", response_model=EnrichmentResultOut)
async def enrich_lead_route(
    request: Request,
    lead_id: str,
    force: bool = Query(False),
) -> EnrichmentResultOut:
    settings: Settings = request.app.state.settings
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        async with s.begin():
            result = await enrich_company(s, settings, lead_id=lead_id, force=force)
        return await _load_lead_view(s, result.lead_id or lead_id)


@router.post("/candidates/{candidate_id}", response_model=EnrichmentResultOut)
async def enrich_candidate_route(
    request: Request,
    candidate_id: str,
    force: bool = Query(False),
) -> EnrichmentResultOut:
    settings: Settings = request.app.state.settings
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        async with s.begin():
            result = await enrich_company(s, settings, candidate_id=candidate_id, force=force)
        cand = (
            await s.execute(select(ShipperCandidate).where(ShipperCandidate.id == candidate_id))
        ).scalar_one_or_none()
        if cand is None:
            raise HTTPException(404, "candidate not found")
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
        return EnrichmentResultOut(
            lead_id=None,
            candidate_id=candidate_id,
            status=cand.enrichment_status or result.status,
            error=cand.enrichment_error,
            is_js_only_site=bool(cand.is_js_only_site),
            decision_makers=[
                DecisionMakerOut(
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
                WebsiteContactOut(
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


@router.get("/leads/{lead_id}", response_model=EnrichmentResultOut)
async def get_lead_enrichment(request: Request, lead_id: str) -> EnrichmentResultOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        return await _load_lead_view(s, lead_id)


# ---------- metrics --------------------------------------------------------


@router.get("/metrics", response_model=EnrichmentMetricsOut)
async def get_metrics(
    request: Request,
    kind: Literal["shipper", "broker"] = Query("shipper"),
) -> EnrichmentMetricsOut:
    sessionmaker = request.app.state.sessionmaker
    kind_val = "Shipper" if kind == "shipper" else "Broker"
    window_days = 30
    async with sessionmaker() as s:
        leads = (await s.execute(select(Lead).where(Lead.kind == kind_val))).scalars().all()
        lead_ids = [lead.id for lead in leads]
        if not lead_ids:
            return EnrichmentMetricsOut(
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
        contacts = (await s.execute(select(LeadContact).where(LeadContact.lead_id.in_(lead_ids)))).scalars().all()
        # Aggregate to per-lead booleans.
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
        # Window-scoped counts key off last_enriched_at (a 30-day active window).
        cutoff = datetime.now(UTC).timestamp() - window_days * 86400
        recent_lead_ids: set[str] = set()
        for lead in leads:
            if lead.last_enriched_at is not None and lead.last_enriched_at.timestamp() >= cutoff:
                recent_lead_ids.add(lead.id)
        enriched = sum(1 for lid in by_lead_has_contact if lid in recent_lead_ids) or enriched_all
        reachable = sum(1 for lid in by_lead_reachable if lid in recent_lead_ids) or reachable_all

        reachable_rate = (reachable / enriched) if enriched else 0.0
        replied_rate = (replied / contacted) if contacted else 0.0
        return EnrichmentMetricsOut(
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


# ---------- auto-outreach --------------------------------------------------


class AutoSendIn(BaseModel):
    dry_run: bool = False
    now_hour_override: int | None = None  # tests can pin the window check


def _within_window(now_hour: int, start_h: int, end_h: int) -> bool:
    if start_h == end_h:
        return True
    if start_h < end_h:
        return start_h <= now_hour < end_h
    # window crosses midnight (e.g. 22..6)
    return now_hour >= start_h or now_hour < end_h


async def _auto_send_impl(request: Request, body: AutoSendIn, *, sender=None) -> AutoSendOut:
    """Thin shim — delegates to `app.outreach.service.auto_send`.

    Kept so the test suite's existing patch surface (`_auto_send_impl`) and
    the compliance-rule docstring stay reachable from the route; new callers
    (the crawl pipeline, future queue jobs) should call the service directly.
    """
    from app.outreach.service import auto_send as _service_auto_send

    settings: Settings = request.app.state.settings
    sessionmaker = request.app.state.sessionmaker
    result = await _service_auto_send(
        sessionmaker=sessionmaker,
        settings=settings,
        dry_run=body.dry_run,
        sender=sender,
        now_hour_override=body.now_hour_override,
    )
    return AutoSendOut(
        status=result.status,
        sent=result.sent,
        skipped_suppressed=result.skipped_suppressed,
        skipped_cap=result.skipped_cap,
        dry_run=result.dry_run,
    )



@router.post("/auto-send", response_model=AutoSendOut)
async def auto_send(
    request: Request,
    body: AutoSendIn,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> AutoSendOut:
    """Route-level auth (BLOCKING-2 fix). Application-level `auto_outreach_enabled`
    is a *what*, not a *who* — this guard makes sure only the cron caller can ask.
    """
    check_secret(request.app.state.settings, x_cron_secret)
    return await _auto_send_impl(request, body)


# ---------- public unsubscribe --------------------------------------------


class UnsubscribeOut(BaseModel):
    ok: bool
    email: str | None
    already: bool


unsub_router = APIRouter(tags=["unsubscribe"])


async def _verify_or_400(request: Request, token: str) -> int:
    settings: Settings = request.app.state.settings
    # Same env → DB precedence the send path signs with, so a link minted from
    # the migration-seeded DB secret actually verifies (no dead links).
    async with request.app.state.sessionmaker() as s:
        cfg = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    secret = effective_secret(settings, cfg)
    if not secret:
        # No secret configured → no minted tokens can be valid → hard 400.
        # A misconfigured deploy must NEVER succeed at mass-unsubscribing.
        raise HTTPException(400, "unsubscribe not configured")
    cid = verify_unsubscribe_token(token, secret)
    if cid is None:
        raise HTTPException(400, "invalid unsubscribe token")
    return cid


@unsub_router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_confirm_page(request: Request, t: str = Query(..., min_length=1)) -> HTMLResponse:
    """GET renders a confirm page — NEVER mutates.

    Email security scanners (Microsoft Safe Links, Google, Proofpoint) auto-fetch
    every URL in every outbound email. If GET mutated, one scanned inbox would
    unsubscribe the recipient before they read the message. Confirm-then-POST is
    the CAN-SPAM one-click contract (RFC 8058); we honor it.
    """
    _ = await _verify_or_400(request, t)
    # Minimal HTML — an autosubmit form is not used; the operator clicks once.
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Unsubscribe — LJM International</title>"
        "<meta name='robots' content='noindex,nofollow'>"
        "</head><body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:1rem'>"
        "<h1>Unsubscribe from LJM International outreach</h1>"
        "<p>Click the button below to stop all future emails to this address.</p>"
        f"<form method='POST' action='/unsubscribe?t={t}'>"
        "<button type='submit' style='padding:0.75rem 1.5rem;font-size:1rem;"
        "background:#0a0a0a;color:#fff;border:0;border-radius:4px'>Confirm unsubscribe</button>"
        "</form>"
        "</body></html>"
    )
    return HTMLResponse(html)


@unsub_router.post("/unsubscribe", response_model=UnsubscribeOut)
async def unsubscribe_post(request: Request, t: str = Query(..., min_length=1)) -> UnsubscribeOut:
    """POST performs the suppression. Same route also accepts RFC 8058
    `List-Unsubscribe=One-Click` payloads via `?t=<token>` on the query string.
    Invalid / forged token → 400 with zero side effects.
    """
    cid = await _verify_or_400(request, t)
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        contact = (await s.execute(select(LeadContact).where(LeadContact.id == cid))).scalar_one_or_none()
        if contact is None:
            raise HTTPException(404, "contact not found")
        email = (contact.email or "").lower() or None
        already = False
        if email:
            existing = (await s.execute(select(Suppression).where(Suppression.email == email))).scalar_one_or_none()
            if existing is None:
                s.add(Suppression(email=email, reason="do_not_contact"))
            else:
                already = True
        if contact.pipeline_status != "lost":
            contact.pipeline_status = "lost"
            contact.pipeline_status_at = datetime.now(UTC)
        await s.commit()
    return UnsubscribeOut(ok=True, email=email, already=already)
