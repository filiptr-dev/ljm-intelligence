"""Backfill + maintain the deterministic fit score on every Lead.

The fit score (``scoring.compute_fit``) is the honest, zero-cost ranking signal.
The AI (Gemini) score is an *optional refinement* layered on top, capped to a
handful of leads per crawl run — so if we lean on ``current_score`` alone, the
UI shows 0 for the long tail. This module gives us two things:

* ``score_all_leads`` — an idempotent, batched walk over the leads table that
  computes the fit score from state + contacts, writes
  ``fit_score``/``fit_reasons``/``fit_computed_at``, and backfills
  ``current_score`` **only when it is NULL** (so a real AI score is never
  clobbered). Returns a stats dict.
* A ``python -m app.prospecting.score_all_leads`` entrypoint with ``--dry-run``
  for one-off ops use and the end-of-crawl ingest hook.

ORM-only — the function takes a sessionmaker and runs its own batches so the
caller doesn't own a long-lived transaction. Each batch commits on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from app.prospecting.models import Lead, LeadContact
from app.prospecting.scoring import DEFAULT_WEIGHTS, build_signals, compute_fit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)


@dataclass
class ScoreStats:
    scanned: int = 0
    updated: int = 0
    skipped_unchanged: int = 0
    buckets: dict[str, int] | None = None
    top: list[dict] | None = None
    distribution_min: int | None = None
    distribution_median: int | None = None
    distribution_max: int | None = None

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "updated": self.updated,
            "skipped_unchanged": self.skipped_unchanged,
            "buckets": self.buckets or {},
            "top": self.top or [],
            "min": self.distribution_min,
            "median": self.distribution_median,
            "max": self.distribution_max,
        }


def _bucket(score: int) -> str:
    if score < 20:
        return "0-20"
    if score < 50:
        return "20-50"
    if score < 80:
        return "50-80"
    return "80-100"


async def _load_contact_rows(session: AsyncSession, lead_ids: list[str]) -> dict[str, list[dict]]:
    if not lead_ids:
        return {}
    rows = (
        await session.execute(select(LeadContact).where(LeadContact.lead_id.in_(lead_ids)))
    ).scalars().all()
    out: dict[str, list[dict]] = {lid: [] for lid in lead_ids}
    for r in rows:
        out.setdefault(r.lead_id, []).append(
            {
                "email": r.email,
                "phone": r.phone,
                "title": r.title,
                "is_dm": bool(r.is_decision_maker),
            }
        )
    return out


async def score_all_leads(
    sessionmaker: async_sessionmaker,
    *,
    batch_size: int = 500,
    dry_run: bool = False,
    only_missing: bool = False,
) -> ScoreStats:
    """Walk every lead in page-sized batches and (re)compute its fit score.

    Each batch is its own transaction so a crash mid-run doesn't lose progress.
    When ``only_missing`` is True (used by the ingest hook), only leads whose
    ``fit_computed_at`` is NULL are touched — the full-sweep CLI uses the
    default (``False``) so weight changes propagate.
    """
    stats = ScoreStats(buckets={"0-20": 0, "20-50": 0, "50-80": 0, "80-100": 0}, top=[])
    scores_seen: list[int] = []
    top_heap: list[tuple[int, str, str, str]] = []  # (score, id, name, state)
    now = datetime.now(UTC)
    offset = 0

    while True:
        async with sessionmaker() as session:
            stmt = select(Lead).order_by(Lead.id).offset(offset).limit(batch_size)
            if only_missing:
                stmt = stmt.where(or_(Lead.fit_computed_at.is_(None), Lead.fit_score.is_(None)))
            page = list((await session.execute(stmt)).scalars().all())
            if not page:
                break

            contacts_by_lead = await _load_contact_rows(session, [lead.id for lead in page])
            dirty = 0
            for lead in page:
                stats.scanned += 1
                signals = build_signals(
                    state=lead.state,
                    contacts=contacts_by_lead.get(lead.id, []),
                    text="",  # website text not persisted; state + contacts are enough to rank
                )
                score, reasons = compute_fit(signals, DEFAULT_WEIGHTS)
                scores_seen.append(score)
                stats.buckets[_bucket(score)] += 1  # type: ignore[index]
                top_heap.append((score, lead.id, lead.name, lead.state))

                changed = (
                    lead.fit_score != score
                    or lead.fit_reasons != reasons
                    or lead.current_score is None
                )
                if changed:
                    dirty += 1
                    if not dry_run:
                        lead.fit_score = score
                        lead.fit_reasons = reasons
                        lead.fit_computed_at = now
                        # Backfill current_score ONLY when no AI score has landed.
                        if lead.current_score is None:
                            lead.current_score = score
                else:
                    stats.skipped_unchanged += 1

            if not dry_run:
                await session.commit()
            stats.updated += dirty

        offset += batch_size

    if scores_seen:
        scores_seen.sort()
        stats.distribution_min = scores_seen[0]
        stats.distribution_max = scores_seen[-1]
        stats.distribution_median = scores_seen[len(scores_seen) // 2]
    top_heap.sort(reverse=True)
    stats.top = [
        {"id": lid, "name": name, "state": state, "score": score}
        for score, lid, name, state in top_heap[:5]
    ]
    return stats


async def _amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        stats = await score_all_leads(
            sm,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            only_missing=args.only_missing,
        )
    finally:
        await engine.dispose()

    report = stats.as_dict()
    log.info(
        "score_all_leads: scanned=%d updated=%d dry_run=%s min=%s median=%s max=%s buckets=%s",
        stats.scanned,
        stats.updated,
        args.dry_run,
        report["min"],
        report["median"],
        report["max"],
        report["buckets"],
    )
    for t in report["top"]:
        log.info("  top: %-6d %-40s %s", t["score"], t["name"][:40], t["state"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Recompute the deterministic fit score for every lead.")
    parser.add_argument("--dry-run", action="store_true", help="compute + report, do not write")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="only score leads whose fit_score / fit_computed_at is NULL (used by the crawl hook)",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    raise SystemExit(main())
