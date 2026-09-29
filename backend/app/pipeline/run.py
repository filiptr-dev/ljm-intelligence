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
from app.region import in_region
from app.sources.emails import add_contact_email
from app.sources.fmcsa import fetch_fmcsa

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
            await s.execute(
                update(CrawlRun).where(CrawlRun.id == run_id).values(status="running")
            )
            await s.commit()

    counts = {"discovered": 0, "new": 0, "scored": 0, "auto_contacted": 0, "errors": 0}
    error: str | None = None
    try:
        fmcsa_leads = await fetch_fmcsa(limit=fmcsa_limit)
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


async def get_run(sessionmaker: async_sessionmaker, run_id: str) -> CrawlRun | None:
    async with sessionmaker() as s:
        res = await s.execute(select(CrawlRun).where(CrawlRun.id == run_id))
        return res.scalar_one_or_none()
