"""Idempotent, additive backfill for leads.domain.

Fills ``leads.domain`` from the lead's own ``primary_email`` or, failing
that, from a ``lead_contacts.email`` on the same lead. Never overwrites
an existing value, never touches a free-mail domain, batches commits
every 500 rows.

Run from the project root::

    python -m app.prospecting.leads_domain_backfill

Separate job (not auto-run) — the orchestrator kicks it off after review.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import select, update

from app.prospecting.models import Lead, LeadContact
from app.prospecting.pipeline.shipper_merge import FREE_MAIL_DOMAINS

log = logging.getLogger("leads_domain_backfill")

_BATCH = 500


def _domain_of(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    dom = email.rsplit("@", 1)[1].strip().lower()
    if not dom or dom in FREE_MAIL_DOMAINS:
        return None
    return dom


async def _resolve_candidate(s, lid: str, pe: str | None) -> str | None:
    """Return the candidate (non-free) domain for one lead, or None."""
    d = _domain_of(pe)
    if d is not None:
        return d
    # Fall back to the lead's contacts, newest first.
    contact_rows = (
        await s.execute(
            select(LeadContact.email)
            .where(LeadContact.lead_id == lid)
            .order_by(LeadContact.discovered_at.desc())
        )
    ).all()
    for (cem,) in contact_rows:
        cd = _domain_of(cem)
        if cd:
            return cd
    return None


async def run_on(sm) -> dict:
    """The backfill loop, parameterised on a sessionmaker for testability."""
    updated = 0
    skipped_free = 0
    skipped_shared = 0  # candidate domain mapped to >1 lead in this run
    skipped_taken = 0   # candidate domain already claimed by another lead
    scanned = 0
    async with sm() as s:
        # Phase 1: collect (lead_id, candidate_domain) for every scannable
        # lead WITHOUT assigning anything yet. ``leads.domain`` is UNIQUE
        # — franchises like unishippers.com legitimately produce several
        # leads sharing one real company domain — so assigning as we went
        # would IntegrityError on the second row and roll back the whole
        # transaction. The live-DB crash this fix addresses:
        #   (psycopg2.errors.UniqueViolation) ...
        #   Key (domain)=(unishippers.com) already exists.  (lead DOT-9024552)
        rows = (
            await s.execute(
                select(Lead.id, Lead.primary_email, Lead.domain).where(
                    (Lead.domain.is_(None)) | (Lead.domain == "")
                )
            )
        ).all()
        candidates: list[tuple[str, str]] = []  # (lead_id, candidate_domain)
        domain_count: dict[str, int] = {}
        for lid, pe, dom in rows:
            scanned += 1
            if dom:
                continue  # defensive — the WHERE already filtered.
            d = await _resolve_candidate(s, lid, pe)
            if d is None:
                if pe and "@" in pe:
                    skipped_free += 1
                continue
            candidates.append((lid, d))
            domain_count[d] = domain_count.get(d, 0) + 1

        # Phase 2: fetch the set of domains ALREADY claimed by any lead so
        # we don't collide with a row the normal pipeline or a prior run
        # has set. One SELECT keeps roundtrips O(1).
        if candidates:
            claimed_rows = (
                await s.execute(
                    select(Lead.domain).where(
                        Lead.domain.in_(sorted(domain_count.keys()))
                    )
                )
            ).scalars().all()
            claimed: set[str] = {
                (d or "").strip().lower() for d in claimed_rows if d
            }
        else:
            claimed = set()

        # Phase 3: assign only when the candidate domain is (a) not claimed
        # by any lead AND (b) mapped to exactly one lead in this run.
        # Each update runs inside its own SAVEPOINT so a race with the
        # pipeline (another writer claims the domain between phase 2 and
        # here) rolls back only that row, not the whole batch.
        batch_count = 0
        for lid, d in candidates:
            if d in claimed:
                skipped_taken += 1
                continue
            if domain_count.get(d, 0) > 1:
                skipped_shared += 1
                continue
            try:
                async with s.begin_nested():
                    await s.execute(
                        update(Lead)
                        .where(
                            Lead.id == lid,
                            (Lead.domain.is_(None)) | (Lead.domain == ""),
                        )
                        .values(domain=d)
                    )
                updated += 1
                claimed.add(d)  # guard subsequent iterations in-process
                batch_count += 1
            except Exception as exc:  # noqa: BLE001 — one conflict mustn't kill the run.
                log.warning(
                    "leads_domain_backfill: skip lead=%s domain=%s: %s",
                    lid, d, type(exc).__name__,
                )
                skipped_taken += 1
            if batch_count >= _BATCH:
                await s.commit()
                batch_count = 0
        if batch_count:
            await s.commit()
    log.info(
        "leads_domain_backfill: scanned=%d updated=%d skipped_free=%d "
        "skipped_shared=%d skipped_taken=%d",
        scanned, updated, skipped_free, skipped_shared, skipped_taken,
    )
    return {
        "scanned": scanned,
        "updated": updated,
        "skipped_free": skipped_free,
        "skipped_shared": skipped_shared,
        "skipped_taken": skipped_taken,
    }


async def run() -> dict:
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    sm = create_sessionmaker(engine)
    try:
        return await run_on(sm)
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    out = asyncio.run(run())
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
