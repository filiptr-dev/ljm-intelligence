"""The crawl pipeline: discover → dedupe → persist. Gemini scoring lands in Slice 3."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.models import CrawlRun, Lead, LeadSource
from app.pipeline.shipper_ingest import (
    ingest_osm_incomings,
    project_fmcsa_shippers_to_candidates,
)
from app.region import IN_REGION_STATES, in_region
from app.sources.emails import add_contact_email
from app.sources.fmcsa import fetch_fmcsa
from app.sources.osm_overpass import elements_to_incomings, fetch_overpass_elements

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    id: str
    counts: dict


def _new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:12]


async def run_crawl(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    *,
    trigger: Literal["cron", "on_demand"] = "on_demand",
    fmcsa_limit: int = 500,
    run_id: str | None = None,
) -> RunSummary:
    """Fetch FMCSA -> upsert leads -> Gemini stage -> record CrawlRun. Idempotent re-run.

    If `run_id` is given (e.g. the API's intake step), reuse that row instead of creating a
    second one — keeps `/crawl/runs` free of duplicate rows per trigger.
    """
    from app.pipeline.gemini_stage import run_gemini_stage

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

    counts = {"discovered": 0, "new": 0, "scored": 0, "auto_contacted": 0, "errors": 0}
    error: str | None = None
    try:
        # Slice 1: fetch_fmcsa is now an async generator. The pipeline still
        # consumes one page — the frontier / page loop / per-run caps land in
        # slice 2. `max_pages=1` keeps the contract identical for now.
        fmcsa_leads: list = []
        async for _page in fetch_fmcsa(page_size=fmcsa_limit, max_pages=1):
            fmcsa_leads = _page
            break
        counts["discovered"] += len(fmcsa_leads)
        async with sessionmaker() as s:
            new_ids: list[str] = []
            for lead in fmcsa_leads:
                if not in_region(lead.state):
                    continue
                stmt = pg_insert(Lead).values(
                    id=lead.id,
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
                    },
                ).returning(Lead.id, Lead.first_seen_run_id)
                res = await s.execute(stmt)
                row = res.first()
                if row and row.first_seen_run_id == run_id:
                    new_ids.append(row.id)

                await add_contact_email(
                    s, lead_id=lead.id, email=lead.primary_email, phone=lead.phone, source="FMCSA Census"
                )

                src_ref = lead.mc or lead.dot or lead.id
                await s.execute(
                    pg_insert(LeadSource)
                    .values(
                        lead_id=lead.id,
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
            counts["new"] += len(new_ids)
            await s.commit()

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
            for k, v in shipper_counts.items():
                counts[k] = counts.get(k, 0) + v
        except Exception:
            log.exception("run_crawl: shipper stage failed; FMCSA leads persisted")
            counts["errors"] += 1

        try:
            gemini_counts = await run_gemini_stage(sessionmaker, settings, run_id=run_id, started=started)
            for k, v in gemini_counts.items():
                counts[k] = counts.get(k, 0) + v
        except Exception:
            log.exception("run_crawl: gemini stage failed; FMCSA pass persisted")
            counts["errors"] += 1

    except Exception as exc:
        log.exception("run_crawl failed")
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


async def _run_shipper_stage(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    *,
    fmcsa_leads: list,
    started: datetime,
) -> dict[str, int]:
    """Slice 2b — project FMCSA shippers, then optionally ingest OSM Overpass finds.

    Every candidate — FMCSA or OSM — flows through `match_and_merge` inside
    `shipper_ingest`, so a repeat sighting updates the existing row and a
    cross-source hit unifies onto one row with both sources.

    Overpass is optional (`settings.osm_overpass_enabled`); per-state failures
    log and continue so a bad Overpass day never poisons the crawl.
    """
    totals = {"shippers_projected": 0, "osm_new": 0, "osm_merged": 0, "osm_states_failed": 0}

    # 1) FMCSA-shipper projection. Own its own transaction so a later OSM error
    #    can't roll it back.
    async with sessionmaker() as s:
        proj = await project_fmcsa_shippers_to_candidates(s, fmcsa_leads, started=started)
        await s.commit()
    totals["shippers_projected"] = proj.get("projected_new", 0) + proj.get("projected_updated", 0)

    # 2) OSM Overpass — one query per in-region state, throttled + cached.
    if not settings.osm_overpass_enabled:
        return totals

    # Which states to touch. Explicit override wins; else the full in-region set.
    states = list(settings.osm_overpass_states) if settings.osm_overpass_states else sorted(IN_REGION_STATES)
    if settings.osm_overpass_max_states:
        states = states[: settings.osm_overpass_max_states]

    for state in states:
        try:
            elements = await fetch_overpass_elements(state)
        except Exception:
            log.exception("run_crawl: overpass fetch raised for %s; continuing", state)
            totals["osm_states_failed"] += 1
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
        except Exception:
            log.exception("run_crawl: osm ingest failed for %s; continuing", state)
            totals["osm_states_failed"] += 1

    return totals


async def get_run(sessionmaker: async_sessionmaker, run_id: str) -> CrawlRun | None:
    async with sessionmaker() as s:
        res = await s.execute(select(CrawlRun).where(CrawlRun.id == run_id))
        return res.scalar_one_or_none()
