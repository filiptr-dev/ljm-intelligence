"""Gemini stage: discovery (Search-grounded) + scoring. Fully optional.

Without GEMINI_API_KEY the stage returns zeros and the FMCSA pass still ships,
per the plan's "degrade gracefully" rule.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.models import Lead, LeadSource, Score
from app.region import in_region
from app.scoring.gemini_score import GeminiScorer
from app.sources.emails import add_contact_email
from app.sources.gemini_search import GeminiDiscoverer

log = logging.getLogger(__name__)


_ERROR_TRUNCATE = 500


def _truncate(msg: str, limit: int = _ERROR_TRUNCATE) -> str:
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


def _classify_gemini_error(exc: Exception) -> str:
    """Best-effort exception → status. Quota / rate-limit shapes vary by SDK; keyword-match."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(k in text for k in ("quota", "rate limit", "rate-limit", "resource_exhausted", "429")):
        return "quota_exceeded"
    return "discovery_failed"


async def run_gemini_stage(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    *,
    run_id: str,
    started: datetime,
) -> dict:
    """Discovery + scoring stage.

    Returns `counts` with numeric progress *and* explicit visibility keys:
      - `gemini_status`: "ok" | "no_api_key" | "discovery_failed" | "quota_exceeded" | "scoring_failed"
      - `gemini_error`: truncated exception string (only when status != "ok"/"no_api_key")

    Never rolls back FMCSA work — a failed discovery still commits whatever
    leads or scores landed before the failure.
    """
    counts: dict = {
        "gemini_discovered": 0,
        "gemini_new": 0,
        "scored": 0,
        "gemini_status": "ok",
    }
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    if not key:
        log.info("gemini stage: GEMINI_API_KEY not set, skipping discovery + scoring")
        counts["gemini_status"] = "no_api_key"
        return counts

    import os

    discovery_model = os.environ.get("GEMINI_MODEL_DISCOVERY") or settings.gemini_model
    scoring_model = os.environ.get("GEMINI_MODEL_SCORING") or settings.gemini_model

    discoverer = GeminiDiscoverer(api_key=key, model=discovery_model)
    try:
        discovered = await discoverer.discover(target_count=8)
    except Exception as exc:
        log.exception("gemini discovery failed")
        counts["gemini_status"] = _classify_gemini_error(exc)
        counts["gemini_error"] = _truncate(f"{type(exc).__name__}: {exc}")
        return counts

    counts["gemini_discovered"] = len(discovered)

    new_ids: list[str] = []
    async with sessionmaker() as s:
        for lead in discovered:
            if not in_region(lead.state):
                continue
            ins = pg_insert(Lead).values(
                id=lead.id,
                mc=lead.mc,
                dot=lead.dot,
                domain=lead.domain,
                name=lead.name,
                kind=lead.kind,
                state=lead.state,
                city=lead.city,
                address=None,
                phone=None,
                primary_email=lead.primary_email,
                first_seen_at=started,
                last_seen_at=started,
                first_seen_run_id=run_id,
                last_seen_run_id=run_id,
                raw={"gemini": lead.raw},
                evidence={"citations": lead.citations},
                recommendations=[],
            )
            stmt = ins.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "last_seen_at": started,
                    "last_seen_run_id": run_id,
                    "raw": Lead.raw.op("||")({"gemini": lead.raw}),
                    "evidence": Lead.evidence.op("||")({"citations": lead.citations}),
                    "primary_email": func.coalesce(Lead.primary_email, ins.excluded.primary_email),
                },
            ).returning(Lead.id, Lead.first_seen_run_id)
            res = await s.execute(stmt)
            row = res.first()
            if row and row.first_seen_run_id == run_id:
                new_ids.append(row.id)
            await add_contact_email(s, lead_id=lead.id, email=lead.primary_email, phone=None, source="Gemini Search")
            src_ref = lead.domain or lead.id
            await s.execute(
                pg_insert(LeadSource)
                .values(
                    lead_id=lead.id,
                    source="Gemini Search",
                    source_ref=src_ref,
                    first_seen_at=started,
                    last_seen_at=started,
                    payload={"raw": lead.raw, "citations": lead.citations},
                )
                .on_conflict_do_update(
                    index_elements=["lead_id", "source", "source_ref"],
                    set_={"last_seen_at": started, "payload": {"raw": lead.raw, "citations": lead.citations}},
                )
            )
        counts["gemini_new"] = len(new_ids)
        await s.commit()

    # Cap per run to stay inside Gemini free-tier RPM. Priority: leads first seen in this run,
    # then any older unscored. Free-tier flash is ~10 rpm; keep the batch small + spaced out.
    import asyncio

    score_cap = int(os.environ.get("GEMINI_SCORE_PER_RUN", "10"))
    async with sessionmaker() as s:
        res = await s.execute(
            select(Lead)
            .where(Lead.current_score.is_(None))
            .order_by((Lead.first_seen_run_id == run_id).desc(), Lead.first_seen_at.desc())
            .limit(score_cap)
        )
        unscored = list(res.scalars().all())

    scorer = GeminiScorer(api_key=key, model=scoring_model)
    scored = 0
    first_scoring_exc: Exception | None = None
    for i, lead in enumerate(unscored):
        if i:
            # Space calls at ~1/s to avoid tripping free-tier RPM.
            await asyncio.sleep(1.0)
        try:
            result = await scorer.score(lead)
        except Exception as exc:
            log.exception("gemini scoring failed for %s", lead.id)
            if first_scoring_exc is None:
                first_scoring_exc = exc
            continue
        async with sessionmaker() as s:
            s.add(
                Score(
                    lead_id=lead.id,
                    run_id=run_id,
                    score=result.score,
                    rationale=result.rationale,
                    signals={"signals": result.signals},
                    model=result.model,
                )
            )
            await s.execute(Lead.__table__.update().where(Lead.id == lead.id).values(current_score=result.score))
            await s.commit()
        scored += 1

    counts["scored"] = scored
    # If every scoring call failed and none landed, surface the reason.
    if unscored and scored == 0 and first_scoring_exc is not None:
        counts["gemini_status"] = (
            "quota_exceeded"
            if _classify_gemini_error(first_scoring_exc) == "quota_exceeded"
            else "scoring_failed"
        )
        counts["gemini_error"] = _truncate(f"{type(first_scoring_exc).__name__}: {first_scoring_exc}")
    return counts
