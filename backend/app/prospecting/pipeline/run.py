"""The crawl pipeline: discover → dedupe → persist. Gemini scoring lands in Slice 3."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.integrations.adapters.enrichment.fmcsa import fetch_fmcsa
from app.integrations.adapters.web.emails import add_contact_email
from app.integrations.adapters.web.osm_overpass import elements_to_incomings, fetch_overpass_elements
from app.prospecting.models import CrawlRun, Lead, LeadSource
from app.prospecting.pipeline.shipper_ingest import (
    ingest_osm_incomings,
    project_fmcsa_shippers_to_candidates,
)
from app.shared.region import IN_REGION_STATES, in_region

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    id: str
    counts: dict


def _new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:12]


def _merge_counts(target: dict, incoming: dict) -> None:
    """Merge stage counts. Numbers accumulate; strings overwrite.

    Sub-stage dicts started carrying string keys (``osm_status``, ``osm_error``,
    ``gemini_status``, ``gemini_error``) in slice 3 — plain ``+=`` would trip
    ``TypeError``. This keeps the merge boring and predictable.
    """
    for k, v in incoming.items():
        if isinstance(v, (int, float)):
            target[k] = target.get(k, 0) + v
        else:
            target[k] = v


async def _read_fmcsa_frontier(sessionmaker: async_sessionmaker) -> str | None:
    """Return the max FMCSA ``add_date`` we've already stored, or None.

    ``add_date`` is 8-char ``YYYYMMDD`` text in SODA, so string ``max()`` matches
    chronological max. Portable across Postgres (JSONB ``->'fmcsa'->>'add_date'``)
    and the sqlite used in tests (``json_extract(raw, '$.fmcsa.add_date')``).
    """
    async with sessionmaker() as s:
        bind = s.get_bind()
        dialect = bind.dialect.name
        if dialect == "postgresql":
            stmt = text("SELECT max(raw->'fmcsa'->>'add_date') FROM leads WHERE first_seen_run_id IS NOT NULL")
        else:  # sqlite (tests) — same shape via json_extract
            stmt = text(
                "SELECT max(json_extract(raw, '$.fmcsa.add_date')) FROM leads WHERE first_seen_run_id IS NOT NULL"
            )
        res = await s.execute(stmt)
        val = res.scalar()
    if val is None:
        return None
    v = str(val).strip()
    return v or None


async def run_crawl(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    *,
    trigger: Literal["cron", "on_demand"] = "on_demand",
    fmcsa_limit: int | None = None,
    run_id: str | None = None,
) -> RunSummary:
    """Fetch FMCSA -> upsert leads -> Gemini stage -> record CrawlRun. Idempotent re-run.

    If `run_id` is given (e.g. the API's intake step), reuse that row instead of creating a
    second one — keeps `/crawl/runs` free of duplicate rows per trigger.

    `fmcsa_limit`, when given, caps the FMCSA rows fetched this run (the run stops with
    `fmcsa_stopped_reason="limit"`). `None` = no extra cap: the paginator's page caps
    and wall-clock budget bound the run, exactly as before.
    """
    from app.prospecting.pipeline.gemini_stage import run_gemini_stage

    started = datetime.now(UTC)
    if run_id is None:
        run_id = _new_run_id()
        async with sessionmaker() as s:
            s.add(
                CrawlRun(
                    id=run_id,
                    started_at=started,
                    status="running",
                    kind="fmcsa+gemini",
                    counts={"discovered": 0, "new": 0, "scored": 0, "auto_contacted": 0},
                    trigger=trigger,
                )
            )
            await s.commit()
    else:
        async with sessionmaker() as s:
            await s.execute(update(CrawlRun).where(CrawlRun.id == run_id).values(status="running"))
            await s.commit()

    counts: dict = {
        "discovered": 0,
        "new": 0,
        "scored": 0,
        "auto_contacted": 0,
        "errors": 0,
        # Slice 2 — FMCSA paginator visibility. Every run says *why* it ended.
        "fmcsa_pages": 0,
        "fmcsa_rows_fetched": 0,
        "fmcsa_discovered": 0,
        "fmcsa_new": 0,
        "fmcsa_stopped_reason": "no_pages",
    }
    error: str | None = None
    # Accumulated across all pages — needed downstream (shipper projection).
    fmcsa_leads: list = []
    try:
        # Frontier = the newest ``add_date`` we've already stored (derived from
        # ``leads.raw``, no cursor column). NULL frontier ⇒ first run ⇒ backfill.
        frontier = await _read_fmcsa_frontier(sessionmaker)
        page_cap = settings.fmcsa_backfill_page_cap if frontier is None else settings.fmcsa_per_run_page_cap
        page_size = settings.fmcsa_page_size
        if fmcsa_limit is not None:
            page_size = min(page_size, fmcsa_limit)
        app_token = settings.fmcsa_app_token.get_secret_value() if settings.fmcsa_app_token else None
        budget_s = settings.fmcsa_time_budget_s

        # Wall-clock deadline. We check it *between* pages so an in-flight
        # upsert isn't cancelled mid-transaction — recording partial progress
        # honestly is better than a torn commit.
        deadline = time.monotonic() + budget_s
        stopped_reason = "cap"  # sane default if we exit the loop by hitting page_cap
        pages_seen = 0
        rows_fetched_total = 0
        new_ids_total: list[str] = []

        try:
            async for page_leads in fetch_fmcsa(
                page_size=page_size,
                max_pages=page_cap,
                app_token=app_token,
            ):
                if fmcsa_limit is not None:
                    page_leads = page_leads[: max(0, fmcsa_limit - rows_fetched_total)]
                pages_seen += 1
                rows_fetched_total += len(page_leads)
                fmcsa_leads.extend(page_leads)

                # Persist this page (upsert loop identical to slice-1 shape).
                page_new_ids, page_min_add_date = await _upsert_fmcsa_page(
                    sessionmaker,
                    page_leads,
                    run_id=run_id,
                    started=started,
                )
                new_ids_total.extend(page_new_ids)

                # Caught-up check: page yielded 0 new AND its oldest row is at
                # or older than what we already have. Both conditions matter —
                # a page with 0 new rows near the top could just be a batch of
                # already-seen carriers with newer add_dates than our frontier.
                caught_up = (
                    frontier is not None
                    and len(page_new_ids) == 0
                    and page_min_add_date is not None
                    and page_min_add_date <= frontier
                )
                if caught_up:
                    stopped_reason = "caught_up"
                    break

                if fmcsa_limit is not None and rows_fetched_total >= fmcsa_limit:
                    stopped_reason = "limit"
                    break

                # Wall-clock check between pages — don't start a page we can't
                # afford to finish. Partial progress is already committed.
                if time.monotonic() >= deadline:
                    stopped_reason = "timeout"
                    break
            else:
                # Async-generator exhausted naturally (short page, keyset dead-end,
                # or page_cap reached inside the generator).
                stopped_reason = "cap" if pages_seen >= page_cap else "caught_up"
        except httpx.HTTPError as exc:
            log.warning("run_crawl: fmcsa http error after %d pages: %s", pages_seen, exc)
            stopped_reason = "http_error"
            error = f"fmcsa: {type(exc).__name__}: {exc}"

        counts["fmcsa_pages"] = pages_seen
        counts["fmcsa_rows_fetched"] = rows_fetched_total
        counts["fmcsa_discovered"] = len(fmcsa_leads)
        counts["fmcsa_new"] = len(new_ids_total)
        counts["fmcsa_stopped_reason"] = stopped_reason
        # Keep the legacy top-level keys populated so existing dashboards
        # don't go dark on the day this ships.
        counts["discovered"] += len(fmcsa_leads)
        counts["new"] += len(new_ids_total)

        # Shipper Finder stage — FMCSA-shipper projection + optional OSM Overpass
        # ingest (plan Slice 2b). Wrapped in its own try/except so a source outage
        # never touches the FMCSA pass we just committed.
        try:
            shipper_counts = await _run_shipper_stage(
                sessionmaker,
                settings,
                fmcsa_leads=fmcsa_leads,
                started=started,
            )
            _merge_counts(counts, shipper_counts)
        except Exception:
            log.exception("run_crawl: shipper stage failed; FMCSA leads persisted")
            counts["errors"] += 1

        try:
            gemini_counts = await run_gemini_stage(sessionmaker, settings, run_id=run_id, started=started)
            _merge_counts(counts, gemini_counts)
        except Exception:
            log.exception("run_crawl: gemini stage failed; FMCSA pass persisted")
            counts["errors"] += 1

        # Enrichment stage (LLM scraper plan 2026-09-30). Own try/except so an
        # enrichment failure never touches the FMCSA / shipper / Gemini passes
        # we just committed.
        try:
            from app.prospecting.pipeline.enrichment import discover_new_shippers, run_enrichment_stage

            enrichment_counts = await run_enrichment_stage(sessionmaker, settings, run_id=run_id, started=started)
            _merge_counts(counts, enrichment_counts)
            discovery_counts = await discover_new_shippers(sessionmaker, settings, run_id=run_id)
            _merge_counts(counts, discovery_counts)
        except Exception:
            log.exception("run_crawl: enrichment stage failed; upstream passes persisted")
            counts["errors"] += 1

        # Auto-outreach stage — only fires when `settings.auto_outreach_enabled`
        # is True (default False). Own try/except so a bad send-day never rolls
        # back enrichment. Loud status in `crawl_runs.counts`.
        try:
            from app.identity.models import SettingsRow
            from app.outreach.service import auto_send

            async with sessionmaker() as _s:
                cfg = (await _s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
            if cfg is None or not cfg.auto_outreach_enabled:
                counts["auto_outreach_status"] = "disabled"
            else:
                # Direct service call — no more fake-request `_Req`/`_AppState`
                # dance. The pipeline is not an HTTP route; it talks to the
                # outreach service directly like any other in-process caller.
                result = await auto_send(
                    sessionmaker=sessionmaker, settings=settings, dry_run=False
                )
                counts["auto_outreach_status"] = result.status
                counts["auto_outreach_sent"] = result.sent
                counts["auto_outreach_skipped_suppressed"] = result.skipped_suppressed
                counts["auto_outreach_skipped_cap"] = result.skipped_cap
        except Exception as exc:
            log.exception("run_crawl: auto-outreach failed; enrichment persisted")
            counts["auto_outreach_status"] = "error"
            counts["auto_outreach_error"] = f"{type(exc).__name__}: {exc}"[:500]

    except Exception as exc:
        log.exception("run_crawl failed")
        # Report to Sentry when wired. The ``before_send`` hook stamps
        # tenant_id/request_id/job_id from the shared contextvars so the
        # background-job path lands tagged the same way HTTP errors do.
        from app.shared.sentry import capture_exception

        capture_exception(exc)
        error = str(exc)

    finished = datetime.now(UTC)
    status = "error" if error else "done"
    async with sessionmaker() as s:
        await s.execute(
            update(CrawlRun)
            .where(CrawlRun.id == run_id)
            .values(finished_at=finished, status=status, counts=counts, error=error)
        )
        await s.commit()

    return RunSummary(id=run_id, counts=counts)


async def _upsert_fmcsa_page(
    sessionmaker: async_sessionmaker,
    page_leads: list,
    *,
    run_id: str,
    started: datetime,
) -> tuple[list[str], str | None]:
    """Upsert one page of FMCSA leads. Returns (new_ids, min_add_date_on_page).

    ``min_add_date_on_page`` is the smallest ``add_date`` from the page's raw
    rows — used by the caller's caught-up check. Return None if the page is
    empty or every row is missing ``add_date``.
    """
    new_ids: list[str] = []
    min_add_date: str | None = None
    # Within one page, remember which id we settled on for each dot/mc/domain so
    # two incoming rows that share a key (same DOT emitted twice, say) merge
    # onto the first one's id instead of racing for a new row — a DB lookup
    # alone wouldn't see the still-uncommitted siblings in this session.
    in_page_by_key: dict[tuple[str, str], str] = {}
    async with sessionmaker() as s:
        for lead in page_leads:
            if not in_region(lead.state):
                continue
            add_date = (lead.raw or {}).get("add_date")
            if add_date:
                s_ad = str(add_date)
                if min_add_date is None or s_ad < min_add_date:
                    min_add_date = s_ad

            # Re-publish guard: FMCSA sometimes reissues a DOT (or MC, or a
            # recovered domain) under a *new* MC — our generated `lead.id`
            # changes (e.g. ``DOT-6315665`` → ``MC-78836882``) but ``leads``
            # has partial-unique indexes on dot/mc/domain, so a plain
            # ``on_conflict (id)`` insert aborts the whole page's transaction
            # with ``UniqueViolation`` and the crawl records a `status=error`
            # with ``fmcsa_pages=0``. Prefer the existing row's id so the
            # upsert merges onto it instead of trying to create a sibling.
            effective_id = lead.id
            for key_col, key_val in (("dot", lead.dot), ("mc", lead.mc), ("domain", lead.domain)):
                if not key_val:
                    continue
                # First check within this page (uncommitted siblings).
                in_page = in_page_by_key.get((key_col, key_val))
                if in_page and in_page != lead.id:
                    effective_id = in_page
                    break
                existing = await s.execute(select(Lead.id).where(getattr(Lead, key_col) == key_val))
                found = existing.scalar_one_or_none()
                if found and found != lead.id:
                    effective_id = found
                    break
            # Remember the id we picked under every key this row carries, so
            # the next row in the page lands on the same row.
            for key_col, key_val in (("dot", lead.dot), ("mc", lead.mc), ("domain", lead.domain)):
                if key_val:
                    in_page_by_key.setdefault((key_col, key_val), effective_id)

            # Per-row savepoint: a UniqueViolation on any other partial-unique
            # key we didn't anticipate rolls back just this row, not the whole
            # page — the crawl keeps progressing instead of recording a
            # status=error with fmcsa_pages=0.
            try:
                async with s.begin_nested():
                    stmt = pg_insert(Lead).values(
                        id=effective_id,
                        mc=lead.mc,
                        dot=lead.dot,
                        domain=lead.domain,
                        name=lead.name,
                        kind=lead.kind,
                        state=lead.state,
                        city=lead.city,
                        address=lead.address,
                        phone=lead.phone,
                        primary_email=lead.primary_email,
                        first_seen_at=started,
                        last_seen_at=started,
                        first_seen_run_id=run_id,
                        last_seen_run_id=run_id,
                        raw={"fmcsa": lead.raw},
                        evidence={},
                        recommendations=[],
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "last_seen_at": started,
                            "last_seen_run_id": run_id,
                            "raw": Lead.raw.op("||")({"fmcsa": lead.raw}),
                            # fill a missing email, never blank out one we already have
                            "primary_email": func.coalesce(Lead.primary_email, stmt.excluded.primary_email),
                            # backfill mc/dot/domain only when currently NULL,
                            # so a re-publish never clobbers the stable key
                            # already stored.
                            "mc": func.coalesce(Lead.mc, stmt.excluded.mc),
                            "dot": func.coalesce(Lead.dot, stmt.excluded.dot),
                            "domain": func.coalesce(Lead.domain, stmt.excluded.domain),
                        },
                    ).returning(Lead.id, Lead.first_seen_run_id)
                    res = await s.execute(stmt)
                    row = res.first()
                    if row and row.first_seen_run_id == run_id:
                        new_ids.append(row.id)

                    await add_contact_email(
                        s,
                        lead_id=effective_id,
                        email=lead.primary_email,
                        phone=lead.phone,
                        source="FMCSA Census",
                    )

                    src_ref = lead.mc or lead.dot or effective_id
                    await s.execute(
                        pg_insert(LeadSource)
                        .values(
                            lead_id=effective_id,
                            source="FMCSA Census",
                            source_ref=src_ref,
                            first_seen_at=started,
                            last_seen_at=started,
                            payload=lead.raw,
                        )
                        .on_conflict_do_update(
                            index_elements=["lead_id", "source", "source_ref"],
                            set_={"last_seen_at": started, "payload": lead.raw},
                        )
                    )
            except IntegrityError:
                # Known shape is the DOT/MC/domain partial-unique collision we
                # just guarded against; log + skip so one bad row never costs
                # us the whole page.
                log.warning(
                    "fmcsa upsert skipped on IntegrityError lead_id=%s dot=%s mc=%s",
                    lead.id, lead.dot, lead.mc,
                )
        await s.commit()
    return new_ids, min_add_date


async def _run_shipper_stage(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    *,
    fmcsa_leads: list,
    started: datetime,
) -> dict:
    """Slice 2b — project FMCSA shippers, then optionally ingest OSM Overpass finds.

    Every candidate — FMCSA or OSM — flows through `match_and_merge` inside
    `shipper_ingest`, so a repeat sighting updates the existing row and a
    cross-source hit unifies onto one row with both sources.

    Overpass is optional (`settings.osm_overpass_enabled`); per-state failures
    log and continue so a bad Overpass day never poisons the crawl. The stage
    surfaces its outcome via ``osm_status`` (``ok`` | ``partial`` | ``all_failed``
    | ``skipped``) and a truncated ``osm_error`` for the first failure.
    """
    totals: dict = {
        "shippers_projected": 0,
        "osm_new": 0,
        "osm_merged": 0,
        "osm_states_failed": 0,
        "osm_status": "ok",
    }

    # 1) FMCSA-shipper projection. Own its own transaction so a later OSM error
    #    can't roll it back.
    async with sessionmaker() as s:
        proj = await project_fmcsa_shippers_to_candidates(s, fmcsa_leads, started=started)
        await s.commit()
    totals["shippers_projected"] = proj.get("projected_new", 0) + proj.get("projected_updated", 0)

    # 2) OSM Overpass — one query per in-region state, throttled + cached.
    if not settings.osm_overpass_enabled:
        totals["osm_status"] = "skipped"
        return totals

    # Which states to touch. Explicit override wins; else the full in-region set.
    states = list(settings.osm_overpass_states) if settings.osm_overpass_states else sorted(IN_REGION_STATES)
    if settings.osm_overpass_max_states:
        states = states[: settings.osm_overpass_max_states]

    states_attempted = 0
    first_osm_error: str | None = None

    for state in states:
        states_attempted += 1
        try:
            # ``raise_on_error=True`` — we want the exception here so we can
            # count it. The source's default (return []) hides silent failures.
            elements = await fetch_overpass_elements(state, raise_on_error=True)
        except Exception as exc:
            log.exception("run_crawl: overpass fetch raised for %s; continuing", state)
            totals["osm_states_failed"] += 1
            if first_osm_error is None:
                first_osm_error = f"{state}: {type(exc).__name__}: {exc}"[:500]
            continue
        if not elements:
            continue
        incomings = elements_to_incomings(elements, state)
        if not incomings:
            continue
        try:
            async with sessionmaker() as s:
                osm_counts = await ingest_osm_incomings(s, incomings, started=started)
                await s.commit()
            totals["osm_new"] += osm_counts.get("osm_new", 0)
            totals["osm_merged"] += osm_counts.get("osm_merged", 0)
        except Exception as exc:
            log.exception("run_crawl: osm ingest failed for %s; continuing", state)
            totals["osm_states_failed"] += 1
            if first_osm_error is None:
                first_osm_error = f"{state}: {type(exc).__name__}: {exc}"[:500]

    failed = totals["osm_states_failed"]
    if states_attempted == 0:
        totals["osm_status"] = "skipped"
    elif failed == 0:
        totals["osm_status"] = "ok"
    elif failed == states_attempted:
        totals["osm_status"] = "all_failed"
    else:
        totals["osm_status"] = "partial"
    if first_osm_error is not None:
        totals["osm_error"] = first_osm_error

    return totals


async def get_run(sessionmaker: async_sessionmaker, run_id: str) -> CrawlRun | None:
    async with sessionmaker() as s:
        res = await s.execute(select(CrawlRun).where(CrawlRun.id == run_id))
        return res.scalar_one_or_none()


async def abort_crawl_run(
    sessionmaker: async_sessionmaker,
    run_id: str,
    *,
    tenant_id: str | None,
    error: str,
) -> None:
    """Move a still-open (`queued`/`running`) crawl run to `error`.

    Used when the job carrying the run dies before `run_crawl` writes its own
    terminal status (cancelled, or failed by the drain's stalled-job recovery).
    A run that already reached `done`/`error` is left untouched.
    """
    from app.shared.db import uow
    from app.shared.tenant import TenantId

    stmt = (
        update(CrawlRun)
        .where(CrawlRun.id == run_id, CrawlRun.status.in_(("queued", "running")))
        .values(status="error", finished_at=datetime.now(UTC), error=error[:500])
    )
    if tenant_id:
        async with uow(sessionmaker, TenantId(tenant_id)) as s:
            await s.execute(stmt)
    else:
        async with sessionmaker() as s:
            await s.execute(stmt)
            await s.commit()
