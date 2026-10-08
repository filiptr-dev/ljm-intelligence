"""Enrichment orchestrator — LinkedIn discovery + company page + website contacts.

Composed of three stages per company:
  1. `find_company_page` (grounded search) → linkedin_company_url + website_url
  2. `find_decision_makers` (grounded search) → LinkedIn decision-maker rows
  3. `site_scraper` on the domain we know (candidate.domain or discovered website_url)
     → website emails/phones/people

Loud failures: each stage sets its own `status` on the returned dataclass; a
sibling stage's failure never aborts the others. All DB writes happen inside a
single `async with s.begin():` transaction on the caller's session.

**Save-everything doctrine (scope change 2026-09-30):** contacts are upserted
via the ladder `email → phone-digits → normalized(name+title)`, but every
sighting *always* gets a `lead_contact_provenance` row — history is never
pruned or overwritten. `last_verified_at` is bumped on each sighting so the
UI can show "sighted 3 times / last confirmed today".

Two hooks:
  * `enrich_company(session, ..., ref_kind='lead'|'candidate')` — single-company,
    called by the on-demand API routes.
  * `run_enrichment_stage(sessionmaker, settings, run_id, started)` — batch,
    wired into `pipeline/run.py`. Priority is **shipper + broker equally**
    (scope change 2026-09-30 — see plan Open Question 2): rows alternate by
    kind up to `enrichment_per_run_cap`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import (
    EnrichmentCandidate,
    FitScoreHistory,
    Lead,
    LeadContact,
    LeadContactProvenance,
    SettingsRow,
    ShipperCandidate,
)
from app.pipeline.shipper_merge import normalize_name
from app.shared.region import IN_REGION_STATES
from app.scoring.fit_score import DEFAULT_WEIGHTS, build_signals, compute_fit
from app.sources.emails import normalize_email
from app.sources.fetcher import Fetcher, HttpxTrafilaturaFetcher
from app.sources.linkedin_search import (
    CompanyRef,
    find_company_page,
    find_decision_makers,
)
from app.integrations.adapters.ai.provider import LLMProvider, NullProvider, get_for
from app.sources.site_scraper import scrape_site

log = logging.getLogger(__name__)

_ERROR_TRUNCATE = 500


def _truncate(msg: str, limit: int = _ERROR_TRUNCATE) -> str:
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


@dataclass(frozen=True, slots=True)
class ContactPayload:
    name: str | None
    title: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None
    is_decision_maker: bool
    confidence: str  # 'high' | 'medium' | 'low'
    source: str
    source_url: str
    snippet: str | None = None
    citations: list[dict] | None = None
    evidence: dict | None = None


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    lead_id: str | None
    candidate_id: str | None
    status: str
    error: str | None
    is_js_only_site: bool
    decision_makers: int
    contacts: int
    pages_fetched: int
    linkedin_company_url: str | None
    website_url: str | None
    ran_at: datetime
    per_stage: dict[str, str] = field(default_factory=dict)


# ---------- contact dedupe / upsert -----------------------------------------


def _dedupe_key_email(email: str | None) -> str | None:
    if not email:
        return None
    return f"e:{email.lower()}"


def _dedupe_key_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"p:{digits}"


def _dedupe_key_nametitle(name: str | None, title: str | None) -> str | None:
    if not name or not title:
        return None
    n = normalize_name(name)
    t = normalize_name(title)
    if not n or not t:
        return None
    return f"nt:{n}|{t}"


async def _find_existing_contact(session: AsyncSession, lead_id: str, payload: ContactPayload) -> LeadContact | None:
    """Ladder: email(lower) → phone-digits → (norm_name+norm_title)."""
    # email
    if payload.email:
        res = await session.execute(
            select(LeadContact).where(
                LeadContact.lead_id == lead_id,
                func.lower(LeadContact.email) == payload.email.lower(),
            )
        )
        row = res.scalar_one_or_none()
        if row:
            return row
    # phone
    key = _dedupe_key_phone(payload.phone)
    if key:
        digits = key[2:]
        res = await session.execute(select(LeadContact).where(LeadContact.lead_id == lead_id))
        for r in res.scalars().all():
            if _dedupe_key_phone(r.phone) == f"p:{digits}":
                return r
    # name+title
    key = _dedupe_key_nametitle(payload.name, payload.title)
    if key:
        res = await session.execute(select(LeadContact).where(LeadContact.lead_id == lead_id))
        for r in res.scalars().all():
            if _dedupe_key_nametitle(r.name, r.title) == key:
                return r
    return None


async def upsert_lead_contact(
    session: AsyncSession,
    *,
    lead_id: str,
    payload: ContactPayload,
    run_id: str | None,
    now: datetime,
) -> tuple[LeadContact, bool]:
    """Merge (prefer-non-null) or insert, then always append a provenance row."""
    existing = await _find_existing_contact(session, lead_id, payload)
    inserted = False
    if existing is None:
        contact = LeadContact(
            lead_id=lead_id,
            name=payload.name,
            title=payload.title,
            email=payload.email,
            phone=payload.phone,
            source=payload.source,
            source_url=payload.source_url,
            linkedin_url=payload.linkedin_url,
            is_decision_maker=payload.is_decision_maker,
            confidence=payload.confidence,
            evidence=payload.evidence,
            last_verified_at=now,
            pipeline_status="found",
            pipeline_status_at=now,
        )
        session.add(contact)
        await session.flush()
        inserted = True
    else:
        contact = existing
        # Prefer-non-null merge — never blank out.
        if payload.name and not contact.name:
            contact.name = payload.name
        if payload.title and not contact.title:
            contact.title = payload.title
        if payload.email and not contact.email:
            contact.email = payload.email
        if payload.phone and not contact.phone:
            contact.phone = payload.phone
        if payload.linkedin_url and not contact.linkedin_url:
            contact.linkedin_url = payload.linkedin_url
        if payload.is_decision_maker:
            contact.is_decision_maker = True
        contact.last_verified_at = now

    # Save-everything: always a new provenance row per sighting.
    session.add(
        LeadContactProvenance(
            contact_id=contact.id,
            source=payload.source,
            source_url=payload.source_url,
            snippet=payload.snippet,
            citations=payload.citations,
            run_id=run_id,
        )
    )
    return contact, inserted


async def upsert_candidate_contact(
    session: AsyncSession,
    *,
    candidate_id: str,
    payload: ContactPayload,
) -> tuple[EnrichmentCandidate, bool]:
    """`enrichment_candidates` variant — same dedupe ladder, no provenance sidecar (kept on promote)."""
    # Ladder — done in-memory over the (small) per-candidate set.
    rows = (
        (await session.execute(select(EnrichmentCandidate).where(EnrichmentCandidate.candidate_id == candidate_id)))
        .scalars()
        .all()
    )
    match: EnrichmentCandidate | None = None
    for r in rows:
        if payload.email and r.email and r.email.lower() == payload.email.lower():
            match = r
            break
    if match is None and payload.phone:
        pd = _dedupe_key_phone(payload.phone)
        for r in rows:
            if _dedupe_key_phone(r.phone) == pd:
                match = r
                break
    if match is None and payload.name and payload.title:
        nt = _dedupe_key_nametitle(payload.name, payload.title)
        for r in rows:
            if _dedupe_key_nametitle(r.name, r.title) == nt:
                match = r
                break

    if match is None:
        row = EnrichmentCandidate(
            candidate_id=candidate_id,
            name=payload.name,
            title=payload.title,
            email=payload.email,
            phone=payload.phone,
            linkedin_url=payload.linkedin_url,
            is_decision_maker=payload.is_decision_maker,
            confidence=payload.confidence,
            source=payload.source,
            source_url=payload.source_url,
            evidence=payload.evidence,
        )
        session.add(row)
        await session.flush()
        return row, True

    if payload.name and not match.name:
        match.name = payload.name
    if payload.title and not match.title:
        match.title = payload.title
    if payload.email and not match.email:
        match.email = payload.email
    if payload.phone and not match.phone:
        match.phone = payload.phone
    if payload.linkedin_url and not match.linkedin_url:
        match.linkedin_url = payload.linkedin_url
    if payload.is_decision_maker:
        match.is_decision_maker = True
    return match, False


# ---------- single-company enrichment ---------------------------------------


def _ref_from_lead(lead: Lead) -> CompanyRef:
    return CompanyRef(
        name=lead.name,
        state=lead.state,
        kind=lead.kind,
        industry_hint=None,
        domain=lead.domain or lead.website_url,
    )


def _ref_from_candidate(c: ShipperCandidate) -> CompanyRef:
    return CompanyRef(
        name=c.name,
        state=c.state,
        kind="Shipper",
        industry_hint=None,
        domain=c.domain or c.website_url,
    )


def _classify_status(stage_statuses: list[str]) -> str:
    """Roll individual stage statuses into a single row-level status."""
    if not stage_statuses:
        return "skipped"
    if all(s == "no_api_key" for s in stage_statuses):
        return "no_api_key"
    ok = sum(1 for s in stage_statuses if s == "ok")
    if ok == len(stage_statuses):
        return "ok"
    if ok > 0:
        return "partial"
    if "js_only" in stage_statuses:
        return "js_only"
    if "robots_blocked" in stage_statuses:
        return "robots_blocked"
    if "fetch_failed" in stage_statuses:
        return "fetch_failed"
    if "extract_failed" in stage_statuses:
        return "extract_failed"
    return "skipped"


async def _lookup_target(
    session: AsyncSession, *, lead_id: str | None, candidate_id: str | None
) -> tuple[Lead | None, ShipperCandidate | None]:
    lead: Lead | None = None
    cand: ShipperCandidate | None = None
    if lead_id:
        res = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = res.scalar_one_or_none()
    if candidate_id:
        res = await session.execute(select(ShipperCandidate).where(ShipperCandidate.id == candidate_id))
        cand = res.scalar_one_or_none()
    return lead, cand


async def enrich_company(
    session: AsyncSession,
    settings: Settings,
    *,
    lead_id: str | None = None,
    candidate_id: str | None = None,
    provider: LLMProvider | None = None,
    fetcher: Fetcher | None = None,
    run_id: str | None = None,
    force: bool = False,
) -> EnrichmentResult:
    """Enrich one lead or one shipper-candidate. Wraps the writes in one transaction."""
    now = datetime.now(UTC)
    lead, cand = await _lookup_target(session, lead_id=lead_id, candidate_id=candidate_id)
    if lead is None and cand is None:
        raise ValueError("enrich_company: neither lead_id nor candidate_id resolved a row")

    target: Lead | ShipperCandidate = lead or cand  # type: ignore[assignment]
    ref = _ref_from_lead(lead) if lead is not None else _ref_from_candidate(cand)  # type: ignore[arg-type]

    # Cache short-circuit (unless force).
    ttl = timedelta(hours=settings.enrichment_cache_ttl_hours)
    if not force and target.last_enriched_at is not None and (now - target.last_enriched_at) < ttl:
        return EnrichmentResult(
            lead_id=lead.id if lead else None,
            candidate_id=cand.id if cand else None,
            status=target.enrichment_status or "ok",
            error=target.enrichment_error,
            is_js_only_site=bool(target.is_js_only_site),
            decision_makers=0,
            contacts=0,
            pages_fetched=0,
            linkedin_company_url=target.linkedin_company_url,
            website_url=target.website_url,
            ran_at=target.last_enriched_at,
            per_stage={"cache": "hit"},
        )

    provider = provider or get_for(
        "enrichment",
        api_key=settings.gemini_api_key,
        model=settings.gemini_model_enrichment,
    )
    # Short-circuit: no LLM available → mark and return; do NOT walk the site
    # scraper (its extractor would need the same key and every page fetch is wasted).
    if isinstance(provider, NullProvider):
        target.last_enriched_at = now
        target.enrichment_status = "no_api_key"
        target.enrichment_error = None
        return EnrichmentResult(
            lead_id=lead.id if lead else None,
            candidate_id=cand.id if cand else None,
            status="no_api_key",
            error=None,
            is_js_only_site=bool(target.is_js_only_site),
            decision_makers=0,
            contacts=0,
            pages_fetched=0,
            linkedin_company_url=target.linkedin_company_url,
            website_url=target.website_url,
            ran_at=now,
            per_stage={"provider": "no_api_key"},
        )

    owns_fetcher = fetcher is None
    fetcher = fetcher or HttpxTrafilaturaFetcher(timeout_s=settings.enrichment_request_timeout_s)

    stages: dict[str, str] = {}
    dm_new = 0
    contacts_new = 0
    pages_fetched = 0
    js_only_seen = False
    discovered_linkedin: str | None = None
    discovered_website: str | None = None

    try:
        # ---- Stage 1: company page discovery
        company_hits = await find_company_page(ref, provider=provider)
        stages["company_page"] = company_hits.status
        if company_hits.status == "ok":
            if company_hits.linkedin_company_url:
                discovered_linkedin = company_hits.linkedin_company_url
            if company_hits.website_url:
                discovered_website = company_hits.website_url

        # ---- Stage 2: decision-maker discovery
        dm_hits = await find_decision_makers(ref, provider=provider)
        stages["decision_makers"] = dm_hits.status
        if dm_hits.status == "ok":
            for h in dm_hits.people:
                payload = ContactPayload(
                    name=h.name,
                    title=h.title,
                    email=None,
                    phone=None,
                    linkedin_url=h.linkedin_url,
                    is_decision_maker=True,
                    confidence="low",  # LinkedIn-only rows are lowest trust
                    source="Gemini Search (LinkedIn)",
                    source_url=h.evidence_url or h.linkedin_url,
                    citations=dm_hits.citations,
                    evidence={"citations": dm_hits.citations, "titles_matched": [h.title], "model": provider.model},
                )
                if lead is not None:
                    _, inserted = await upsert_lead_contact(
                        session, lead_id=lead.id, payload=payload, run_id=run_id, now=now
                    )
                else:
                    assert cand is not None
                    _, inserted = await upsert_candidate_contact(session, candidate_id=cand.id, payload=payload)
                if inserted:
                    dm_new += 1

        # ---- Stage 3: site scraper
        site_text_aggregate = ""
        site_domain = ref.domain or discovered_website
        if site_domain:
            site_hits = await scrape_site(
                site_domain,
                fetcher=fetcher,
                provider=provider,
                titles_regex=settings.enrichment_titles_regex,
                page_cap=settings.enrichment_page_cap_per_company,
                between_requests_s=settings.enrichment_between_requests_s,
            )
            stages["site"] = site_hits.status
            pages_fetched = site_hits.pages_fetched
            js_only_seen = site_hits.js_only_flags > 0
            site_text_aggregate = site_hits.combined_text or ""
            if site_hits.extraction is not None:
                extraction = site_hits.extraction
                first_page = site_hits.page_provenance[0].url if site_hits.page_provenance else f"https://{site_domain}"
                # emails-only rows
                seen_email_only: set[str] = set()
                for e in extraction.emails:
                    seen_email_only.add(e.value)
                    payload = ContactPayload(
                        name=None,
                        title=None,
                        email=e.value,
                        phone=None,
                        linkedin_url=None,
                        is_decision_maker=False,
                        confidence="high",
                        source="Website Contact Page",
                        source_url=first_page,
                        snippet=e.context,
                    )
                    if lead is not None:
                        _, inserted = await upsert_lead_contact(
                            session, lead_id=lead.id, payload=payload, run_id=run_id, now=now
                        )
                    else:
                        assert cand is not None
                        _, inserted = await upsert_candidate_contact(session, candidate_id=cand.id, payload=payload)
                    if inserted:
                        contacts_new += 1
                for p in extraction.people:
                    is_dm = True
                    conf = "high" if p.email else "medium"
                    payload = ContactPayload(
                        name=p.name,
                        title=p.title,
                        email=p.email,
                        phone=p.phone,
                        linkedin_url=None,
                        is_decision_maker=is_dm,
                        confidence=conf,
                        source="Website Team Page",
                        source_url=first_page,
                    )
                    if lead is not None:
                        _, inserted = await upsert_lead_contact(
                            session, lead_id=lead.id, payload=payload, run_id=run_id, now=now
                        )
                    else:
                        assert cand is not None
                        _, inserted = await upsert_candidate_contact(session, candidate_id=cand.id, payload=payload)
                    if inserted:
                        contacts_new += 1

        # Roll status + persist target-row book-keeping
        stage_list = list(stages.values())
        row_status = _classify_status(stage_list)
        target.last_enriched_at = now
        target.enrichment_status = row_status
        target.enrichment_error = None
        if js_only_seen:
            target.is_js_only_site = True
        if discovered_linkedin and not target.linkedin_company_url:
            target.linkedin_company_url = discovered_linkedin
        if discovered_website and not target.website_url:
            target.website_url = discovered_website

        # ---- Fit-score computation (scope change 2026-09-30) ---------------
        await _compute_and_persist_fit(
            session,
            target=target,
            lead=lead,
            cand=cand,
            site_text=site_text_aggregate,
            now=now,
        )

        return EnrichmentResult(
            lead_id=lead.id if lead else None,
            candidate_id=cand.id if cand else None,
            status=row_status,
            error=None,
            is_js_only_site=bool(target.is_js_only_site),
            decision_makers=dm_new,
            contacts=contacts_new,
            pages_fetched=pages_fetched,
            linkedin_company_url=target.linkedin_company_url,
            website_url=target.website_url,
            ran_at=now,
            per_stage=stages,
        )
    finally:
        if owns_fetcher:
            await fetcher.aclose()


# ---------- fit-score computation ------------------------------------------


async def _compute_and_persist_fit(
    session: AsyncSession,
    *,
    target: Lead | ShipperCandidate,
    lead: Lead | None,
    cand: ShipperCandidate | None,
    site_text: str,
    now: datetime,
) -> tuple[int, list[str]]:
    """Compute fit for `target` using its current contacts + `site_text` signals.

    Reads weights off the singleton `settings` row (if present); falls back to
    `DEFAULT_WEIGHTS`. Every computation is appended to `fit_score_history` —
    save-everything doctrine.
    """
    # Load fresh contacts for the target.
    contacts_out: list[dict] = []
    if lead is not None:
        rows = (await session.execute(select(LeadContact).where(LeadContact.lead_id == lead.id))).scalars().all()
        for r in rows:
            contacts_out.append(
                {
                    "email": r.email,
                    "phone": r.phone,
                    "title": r.title,
                    "is_dm": bool(r.is_decision_maker),
                }
            )
    elif cand is not None:
        rows = (
            (await session.execute(select(EnrichmentCandidate).where(EnrichmentCandidate.candidate_id == cand.id)))
            .scalars()
            .all()
        )
        for r in rows:
            contacts_out.append(
                {
                    "email": r.email,
                    "phone": r.phone,
                    "title": r.title,
                    "is_dm": bool(r.is_decision_maker),
                }
            )

    signals = build_signals(state=target.state, contacts=contacts_out, text=site_text)
    cfg = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    weights = (cfg.fit_weights if cfg else None) or DEFAULT_WEIGHTS
    score, reasons = compute_fit(signals, weights)

    target.fit_score = score
    target.fit_reasons = reasons
    target.fit_computed_at = now

    session.add(
        FitScoreHistory(
            lead_id=lead.id if lead else None,
            candidate_id=cand.id if cand else None,
            score=score,
            reasons=reasons,
            signals={
                "state": signals.state,
                "has_named_dm_direct": signals.has_named_decision_maker_with_direct_contact,
                "has_only_generic_email": signals.has_only_generic_email,
                "has_warehouse_or_dc": signals.has_warehouse_or_dc_mention,
                "ships_nationwide": signals.ships_nationwide,
                "industry_freight_heavy": signals.industry_freight_heavy,
                "equipment_hint": signals.equipment_dry_van_or_reefer_or_flatbed,
                "locations_count": signals.locations_count,
                "has_phone": signals.has_phone,
                "industry_keywords_hit": list(signals.industry_keywords_hit),
                "equipment_keywords_hit": list(signals.equipment_keywords_hit),
            },
            weights=dict(weights),
        )
    )
    return score, reasons


# ---------- crawler-stage hook ----------------------------------------------


async def _pick_leads_batch(session: AsyncSession, batch: int, ttl_hours: int) -> list[Lead]:
    """Fetch the next `batch` unenriched leads (checkpointed by `last_enriched_at`).

    Interleaves Shipper + Broker rows — equal priority (scope change 2026-09-30).
    A Render restart mid-stage picks up exactly where it left off because
    `last_enriched_at` is written per-row at commit time; already-fresh rows
    fall outside the TTL window and are skipped next fetch.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    stmt = (
        select(Lead)
        .where(Lead.state.in_(sorted(IN_REGION_STATES)))
        .where((Lead.last_enriched_at.is_(None)) | (Lead.last_enriched_at < cutoff))
        .order_by(Lead.current_score.desc().nulls_last(), Lead.last_seen_at.desc())
        .limit(batch * 4)
    )
    res = await session.execute(stmt)
    rows = list(res.scalars().all())
    shippers = [r for r in rows if (r.kind or "").lower() == "shipper"]
    brokers = [r for r in rows if (r.kind or "").lower() == "broker"]
    picked: list[Lead] = []
    while len(picked) < batch and (shippers or brokers):
        if shippers:
            picked.append(shippers.pop(0))
            if len(picked) >= batch:
                break
        if brokers:
            picked.append(brokers.pop(0))
    return picked[:batch]


def _is_quota_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(k in text for k in ("quota", "rate limit", "rate-limit", "resource_exhausted", "429"))


async def run_enrichment_stage(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    *,
    run_id: str,
    started: datetime,
    provider: LLMProvider | None = None,
    fetcher: Fetcher | None = None,
    batch_size: int = 5,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    """Unlimited-with-checkpointing enrichment stage (scope change 2026-09-30).

    Walks EVERY not-yet-enriched Shipper + Broker (equal priority, interleaved)
    in small internal batches. Each row commits its own `last_enriched_at`
    before we move on, so a Render restart mid-stage picks up cleanly from
    the tail of unfinished rows on the next run.

    Quota-exhaustion is honored loudly: the first Gemini error whose message
    matches `429 / rate limit / quota / resource_exhausted` stops the whole
    stage and returns `enrichment_status="quota_exhausted"`. Site politeness
    (robots, throttle, 8-page cap, timeouts) is unchanged — unlimited means
    "walk all leads", not "hammer harder".

    `max_iterations` is a test knob (leave None in production).
    """
    counts: dict[str, Any] = {
        "enrichment_companies": 0,
        "enrichment_dm_new": 0,
        "enrichment_contacts_new": 0,
        "enrichment_pages_fetched": 0,
        "enrichment_status": "ok",
    }
    if not settings.enrichment_enabled:
        counts["enrichment_status"] = "skipped"
        return counts

    provider = provider or get_for(
        "enrichment", api_key=settings.gemini_api_key, model=settings.gemini_model_enrichment
    )
    if isinstance(provider, NullProvider):
        counts["enrichment_status"] = "no_api_key"
        return counts

    owns_fetcher = fetcher is None
    fetcher = fetcher or HttpxTrafilaturaFetcher(timeout_s=settings.enrichment_request_timeout_s)

    first_error: str | None = None
    ok_count = 0
    fail_count = 0
    quota_stopped = False
    iteration = 0

    try:
        while True:
            iteration += 1
            if max_iterations is not None and iteration > max_iterations:
                break
            async with sessionmaker() as s:
                picks = await _pick_leads_batch(s, batch_size, settings.enrichment_cache_ttl_hours)
            if not picks:
                break

            for lead in picks:
                try:
                    async with sessionmaker() as s, s.begin():
                        r = await enrich_company(
                            s,
                            settings,
                            lead_id=lead.id,
                            provider=provider,
                            fetcher=fetcher,
                            run_id=run_id,
                        )
                    counts["enrichment_dm_new"] += r.decision_makers
                    counts["enrichment_contacts_new"] += r.contacts
                    counts["enrichment_pages_fetched"] += r.pages_fetched
                    counts["enrichment_companies"] += 1
                    if r.status == "ok":
                        ok_count += 1
                    else:
                        fail_count += 1
                except Exception as exc:
                    log.exception("enrichment: company failed lead=%s", lead.id)
                    fail_count += 1
                    if first_error is None:
                        first_error = f"{type(exc).__name__}: {exc}"
                    if _is_quota_error(exc):
                        quota_stopped = True
                        break
            if quota_stopped:
                break

        if quota_stopped:
            counts["enrichment_status"] = "quota_exhausted"
        elif ok_count == 0 and fail_count == 0 or fail_count == 0:
            counts["enrichment_status"] = "ok"
        elif ok_count == 0:
            counts["enrichment_status"] = "all_failed"
        else:
            counts["enrichment_status"] = "partial"

        if first_error is not None:
            counts["enrichment_error"] = _truncate(first_error)
        return counts
    finally:
        if owns_fetcher:
            await fetcher.aclose()


# ---------- Gemini-discovery new shippers -----------------------------------


async def discover_new_shippers(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    *,
    provider: LLMProvider | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Grounded `industry × state` discovery. Delegates ingest to `shipper_ingest`.

    Uses one industry × state per run (rotated by `run_id`) to stay inside quota.
    Any discovered company flows through `match_and_merge`; new rows land with
    `sources=['Gemini']`.
    """
    from app.pipeline.shipper_ingest import _upsert_candidate  # local — small extension
    from app.pipeline.shipper_merge import IncomingCandidate
    from app.sources.gemini_search import GeminiDiscoverer

    counts: dict[str, Any] = {
        "discovery_new_shippers": 0,
        "discovery_status": "ok",
    }
    provider = provider or get_for(
        "enrichment", api_key=settings.gemini_api_key, model=settings.gemini_model_enrichment
    )
    if isinstance(provider, NullProvider):
        counts["discovery_status"] = "no_api_key"
        return counts

    industries = list(settings.discovery_industries or [])
    if not industries:
        counts["discovery_status"] = "skipped"
        return counts

    # Cheap rotation over industry list — hash the run_id string for determinism.
    idx = (abs(hash(run_id or "0")) % len(industries)) if industries else 0
    industry = industries[idx]
    _ = industry  # (industry hint is baked into the discoverer's LJM_PROFILE by default)

    discoverer = GeminiDiscoverer(
        api_key=(settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""),
        model=settings.gemini_model_enrichment,
    )
    try:
        discovered = await discoverer.discover(target_count=settings.discovery_per_run_cap)
    except Exception as exc:  # noqa: BLE001
        log.info("discovery: fetch failed: %s", exc)
        counts["discovery_status"] = "fetch_failed"
        counts["discovery_error"] = _truncate(f"{type(exc).__name__}: {exc}")
        return counts

    if not discovered:
        counts["discovery_status"] = "ok"
        return counts

    async with sessionmaker() as s:
        for row in discovered:
            if row.kind not in ("Shipper", "Broker", "Forwarder"):
                continue
            inc = IncomingCandidate(
                source="GEMINI",
                name=row.name,
                state=row.state,
                city=row.city,
                domain=row.domain,
            )
            extra = {"raw": row.raw, "primary_email": row.primary_email}
            _, inserted = await _upsert_candidate(s, inc, extra=extra, promoted_lead_id=None, started=datetime.now(UTC))
            await s.flush()
            if inserted:
                counts["discovery_new_shippers"] += 1
        await s.commit()

    return counts


# ---------- promote hook: copy enrichment_candidates into lead_contacts -----


async def copy_enrichment_candidates_to_lead(
    session: AsyncSession,
    *,
    candidate_id: str,
    lead_id: str,
    run_id: str | None,
    now: datetime | None = None,
) -> int:
    """Called from the promote path — copy pre-promotion contacts onto the new lead.

    Merges into any existing `lead_contacts` for the lead using the same ladder
    as `upsert_lead_contact`. Returns the number of rows copied.
    """
    now = now or datetime.now(UTC)
    rows = (
        (await session.execute(select(EnrichmentCandidate).where(EnrichmentCandidate.candidate_id == candidate_id)))
        .scalars()
        .all()
    )
    copied = 0
    for r in rows:
        payload = ContactPayload(
            name=r.name,
            title=r.title,
            email=normalize_email(r.email),
            phone=r.phone,
            linkedin_url=r.linkedin_url,
            is_decision_maker=bool(r.is_decision_maker),
            confidence=r.confidence or "medium",
            source=r.source or "Enrichment Candidate",
            source_url=r.source_url or "",
            evidence=r.evidence,
        )
        await upsert_lead_contact(session, lead_id=lead_id, payload=payload, run_id=run_id, now=now)
        copied += 1
    return copied


# ---------- pipeline_status transitions -------------------------------------


async def mark_contact_contacted(session: AsyncSession, contact_id: int, *, now: datetime | None = None) -> bool:
    """Flip `found → contacted`. Called from the send path (auto-outreach + manual)."""
    now = now or datetime.now(UTC)
    row = (await session.execute(select(LeadContact).where(LeadContact.id == contact_id))).scalar_one_or_none()
    if row is None:
        return False
    if row.pipeline_status == "found":
        row.pipeline_status = "contacted"
        row.pipeline_status_at = now
        return True
    return False


# Re-exports for callers.
Kind = Literal["Shipper", "Broker", "Forwarder"]
