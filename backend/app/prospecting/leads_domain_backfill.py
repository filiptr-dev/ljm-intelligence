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


async def run() -> dict:
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    sm = create_sessionmaker(engine)
    updated = 0
    skipped_free = 0
    scanned = 0
    async with sm() as s:
        # Scan every lead whose domain is NULL or empty — one pass; ORM only.
        rows = (
            await s.execute(
                select(Lead.id, Lead.primary_email, Lead.domain).where(
                    (Lead.domain.is_(None)) | (Lead.domain == "")
                )
            )
        ).all()
        batch_count = 0
        for lid, pe, dom in rows:
            scanned += 1
            if dom:
                continue  # defensive — the WHERE already filtered.
            d = _domain_of(pe)
            if d is None:
                # Try the lead's contacts, newest first.
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
                        d = cd
                        break
            if d is None:
                if pe and "@" in pe:
                    skipped_free += 1
                continue
            await s.execute(
                update(Lead).where(Lead.id == lid, (Lead.domain.is_(None)) | (Lead.domain == "")).values(domain=d)
            )
            updated += 1
            batch_count += 1
            if batch_count >= _BATCH:
                await s.commit()
                batch_count = 0
        if batch_count:
            await s.commit()
    await engine.dispose()
    log.info(
        "leads_domain_backfill: scanned=%d updated=%d skipped_free=%d",
        scanned, updated, skipped_free,
    )
    return {"scanned": scanned, "updated": updated, "skipped_free": skipped_free}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    out = asyncio.run(run())
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
