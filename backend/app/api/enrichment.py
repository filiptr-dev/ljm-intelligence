"""Enrichment routes — thin router over ``app.integrations.enrichment_service``.

Four surfaces behind one ``/enrichment`` prefix (plus the public ``/unsubscribe``
router):

  POST /enrichment/leads/{lead_id}         — on-demand enrich of a lead
  POST /enrichment/candidates/{candidate}  — on-demand enrich of a shipper candidate
  GET  /enrichment/leads/{lead_id}         — latest view
  GET  /enrichment/metrics                 — scope-scoped funnel counts
  POST /enrichment/auto-send               — cron-driven auto-outreach
  GET  /unsubscribe                        — one-click confirm page
  POST /unsubscribe                        — one-click suppress (RFC 8058)

Every route ships an explicit ``response_model=…`` so ``openapi-typescript``
generates readable types for the frontend typed client.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api._auth import check_secret
from app.config import Settings
from app.integrations.enrichment_service import (
    InvalidUnsubscribeTokenError,
    NotFoundError,
    UnsubscribeConfigError,
    apply_unsubscribe as svc_apply_unsubscribe,
    enrich_candidate as svc_enrich_candidate,
    enrich_lead as svc_enrich_lead,
    load_lead_view as svc_load_lead_view,
    metrics as svc_metrics,
    verify_token as svc_verify_token,
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
    "queued",
]


class AutoSendOut(BaseModel):
    status: AutoSendStatus
    sent: int
    skipped_suppressed: int
    skipped_cap: int
    dry_run: bool
    # Set when the auto-send is handed to the worker; the daily-crawl cron
    # workflow polls `/jobs/{id}` for completion. On the inline path (sqlite
    # + tests) this stays null and the sync fields hold the real tally.
    job_id: int | None = None


# ---------- projection ------------------------------------------------------


def _to_out(r) -> EnrichmentResultOut:
    return EnrichmentResultOut(
        lead_id=r.lead_id,
        candidate_id=r.candidate_id,
        status=r.status,
        error=r.error,
        is_js_only_site=r.is_js_only_site,
        decision_makers=[DecisionMakerOut(**d.__dict__) for d in r.decision_makers],
        contacts=[WebsiteContactOut(**c.__dict__) for c in r.contacts],
        pages_fetched=r.pages_fetched,
        linkedin_company_url=r.linkedin_company_url,
        website_url=r.website_url,
        ran_at=r.ran_at,
        per_stage=r.per_stage,
        fit_score=r.fit_score,
        fit_reasons=r.fit_reasons,
    )


# ---------- on-demand routes -----------------------------------------------


@router.post("/leads/{lead_id}", response_model=EnrichmentResultOut)
async def enrich_lead_route(
    request: Request,
    lead_id: str,
    force: bool = Query(False),
) -> EnrichmentResultOut:
    settings: Settings = request.app.state.settings
    try:
        row = await svc_enrich_lead(
            request.app.state.sessionmaker, settings, lead_id, force=force
        )
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _to_out(row)


@router.post("/candidates/{candidate_id}", response_model=EnrichmentResultOut)
async def enrich_candidate_route(
    request: Request,
    candidate_id: str,
    force: bool = Query(False),
) -> EnrichmentResultOut:
    settings: Settings = request.app.state.settings
    try:
        row = await svc_enrich_candidate(
            request.app.state.sessionmaker, settings, candidate_id, force=force
        )
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _to_out(row)


@router.get("/leads/{lead_id}", response_model=EnrichmentResultOut)
async def get_lead_enrichment(request: Request, lead_id: str) -> EnrichmentResultOut:
    async with request.app.state.sessionmaker() as s:
        try:
            row = await svc_load_lead_view(s, lead_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    return _to_out(row)


# ---------- metrics --------------------------------------------------------


@router.get("/metrics", response_model=EnrichmentMetricsOut)
async def get_metrics(
    request: Request,
    kind: Literal["shipper", "broker"] = Query("shipper"),
) -> EnrichmentMetricsOut:
    async with request.app.state.sessionmaker() as s:
        row = await svc_metrics(s, kind)
    return EnrichmentMetricsOut(
        scope=kind,  # narrow literal; svc returns matching str
        window_days=row.window_days,
        enriched=row.enriched,
        reachable=row.reachable,
        contacted=row.contacted,
        replied=row.replied,
        enriched_all_time=row.enriched_all_time,
        reachable_all_time=row.reachable_all_time,
        reachable_rate=row.reachable_rate,
        replied_rate=row.replied_rate,
    )


# ---------- auto-outreach --------------------------------------------------


class AutoSendIn(BaseModel):
    dry_run: bool = False
    now_hour_override: int | None = None  # tests can pin the window check


async def _auto_send_impl(request: Request, body: AutoSendIn, *, sender=None) -> AutoSendOut:
    """Thin shim — delegates to ``app.outreach.service.auto_send``.

    Kept so the test suite's existing patch surface (``_auto_send_impl``) and
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
    background: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> AutoSendOut:
    """Route-level auth (BLOCKING-2 fix). Application-level
    ``auto_outreach_enabled`` is a *what*, not a *who* — this guard ensures
    only the cron caller can ask."""
    check_secret(request.app.state.settings, x_cron_secret)

    settings = request.app.state.settings

    from app.shared.orm import LJM_TENANT_ID
    from app.shared.queue_dispatch import maybe_dispatch

    job_id = await maybe_dispatch(
        request.app.state.sessionmaker,
        "outreach.auto_send",
        tenant_id=LJM_TENANT_ID,
        dry_run=body.dry_run,
    )
    if job_id is not None:
        from app.api.jobs import kick_in_process_drain
        background.add_task(
            kick_in_process_drain, request.app.state.sessionmaker, settings,
            seconds=settings.jobs_in_process_kick_seconds,
        )
        return AutoSendOut(
            status="queued", sent=0, skipped_suppressed=0, skipped_cap=0,
            dry_run=body.dry_run, job_id=job_id,
        )

    return await _auto_send_impl(request, body)


# ---------- public unsubscribe --------------------------------------------


class UnsubscribeOut(BaseModel):
    ok: bool
    email: str | None
    already: bool


unsub_router = APIRouter(tags=["unsubscribe"])


async def _verify_or_400(request: Request, token: str):
    """Return the verified :class:`UnsubscribeTarget` or raise 400."""
    try:
        return await svc_verify_token(
            request.app.state.sessionmaker, request.app.state.settings, token
        )
    except UnsubscribeConfigError as exc:
        raise HTTPException(400, "unsubscribe not configured") from exc
    except InvalidUnsubscribeTokenError as exc:
        raise HTTPException(400, "invalid unsubscribe token") from exc


@unsub_router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_confirm_page(request: Request, t: str = Query(..., min_length=1)) -> HTMLResponse:
    """GET renders a confirm page — NEVER mutates.

    Email security scanners (Microsoft Safe Links, Google, Proofpoint) auto-fetch
    every URL in every outbound email. If GET mutated, one scanned inbox would
    unsubscribe the recipient before they read the message. Confirm-then-POST is
    the CAN-SPAM one-click contract (RFC 8058); we honor it.
    """
    _ = await _verify_or_400(request, t)
    # Escape the token before echoing it back into HTML. Even though
    # `_verify_or_400` has already verified the token signature, the raw
    # string can still contain ``<``/``>`` characters that would break out
    # of the attribute context on a malformed input — defence in depth.
    import html as _htmllib

    t_safe = _htmllib.escape(t, quote=True)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Unsubscribe — LJM International</title>"
        "<meta name='robots' content='noindex,nofollow'>"
        "</head><body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:1rem'>"
        "<h1>Unsubscribe from LJM International outreach</h1>"
        "<p>Click the button below to stop all future emails to this address.</p>"
        f"<form method='POST' action='/unsubscribe?t={t_safe}'>"
        "<button type='submit' style='padding:0.75rem 1.5rem;font-size:1rem;"
        "background:#0a0a0a;color:#fff;border:0;border-radius:4px'>Confirm unsubscribe</button>"
        "</form>"
        "</body></html>"
    )
    return HTMLResponse(html)


@unsub_router.post("/unsubscribe", response_model=UnsubscribeOut)
async def unsubscribe_post(request: Request, t: str = Query(..., min_length=1)) -> UnsubscribeOut:
    """POST performs the suppression. Same route also accepts RFC 8058
    ``List-Unsubscribe=One-Click`` payloads via ``?t=<token>`` on the query
    string. Invalid / forged token → 400 with zero side effects."""
    target = await _verify_or_400(request, t)
    try:
        result = await svc_apply_unsubscribe(request.app.state.sessionmaker, target)
    except NotFoundError as exc:
        raise HTTPException(404, "contact not found") from exc
    return UnsubscribeOut(ok=result.ok, email=result.email, already=result.already)
