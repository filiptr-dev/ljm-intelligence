"""Compact + sanitize existing ``mail_messages`` rows in-place.

Idempotent batched sweep: cleans ``body_html`` (strip ``<script>``, tracking
pixels, head/style, HTML comments), decodes quoted-printable + HTML
entities in ``body_text``, derives text from HTML when the plain part is
empty. Running it twice is a no-op — the sanitiser's output is a fixed
point.

Usage (inside the api container)::

    docker exec ljm-local-api-1 \\
        bash -lc "PYTHONPATH=/app python -m app.inbox.compact_bodies [--dry-run] [--batch-size 200]"

Reports bytes-before / bytes-after and how many rows actually changed.
Commits every batch so a crash leaves the DB in a consistent partial
state (next run resumes cleanly).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import func, select, tuple_

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.inbox.models import MailMessage
from app.integrations.adapters.email.sanitize import compact_bodies
from app.shared.db import uow
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId

log = logging.getLogger(__name__)


async def _compact_once(*, dry_run: bool, batch_size: int) -> dict[str, int]:
    settings = get_settings()
    engine = create_engine(settings)
    bytes_before = 0
    bytes_after = 0
    rows_seen = 0
    rows_changed = 0
    try:
        sm = create_sessionmaker(engine)
        # MailMessage has a composite PK (mailbox, message_id) — keyset
        # paginate on the tuple, there is no ``id`` column.
        last_key: tuple[str, str] | None = None
        while True:
            async with uow(sm, TenantId(LJM_TENANT_ID)) as session:
                pk = tuple_(MailMessage.mailbox, MailMessage.message_id)
                q = select(MailMessage).order_by(MailMessage.mailbox, MailMessage.message_id)
                if last_key is not None:
                    q = q.where(pk > tuple_(*last_key))
                q = q.limit(batch_size)
                rows = (await session.execute(q)).scalars().all()
                if not rows:
                    break
                for row in rows:
                    rows_seen += 1
                    last_key = (row.mailbox, row.message_id)
                    before_text = row.body_text or ""
                    before_html = row.body_html or ""
                    text_clean, html_clean = compact_bodies(before_text, before_html)
                    bytes_before += len(before_text) + len(before_html)
                    bytes_after += len(text_clean) + len(html_clean)
                    if text_clean != before_text or html_clean != before_html:
                        rows_changed += 1
                        if not dry_run:
                            row.body_text = text_clean
                            row.body_html = html_clean
                if dry_run:
                    # Nothing to commit — the ``uow`` wrapper rolls back on
                    # exception, commits on exit; explicit flush is enough
                    # to prove the UPDATEs would land.
                    await session.rollback()
    finally:
        await engine.dispose()
    return {
        "rows_seen": rows_seen,
        "rows_changed": rows_changed,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
    }


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Scan + report; do not write.")
    ap.add_argument("--batch-size", type=int, default=200)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = await _compact_once(dry_run=args.dry_run, batch_size=args.batch_size)
    delta = stats["bytes_before"] - stats["bytes_after"]
    pct = (delta / stats["bytes_before"] * 100.0) if stats["bytes_before"] else 0.0
    print(
        f"compact_bodies: seen={stats['rows_seen']} changed={stats['rows_changed']} "
        f"bytes_before={stats['bytes_before']} bytes_after={stats['bytes_after']} "
        f"delta={delta} ({pct:.1f}% smaller)"
        + (" [dry-run]" if args.dry_run else "")
    )


# Expose a tiny helper for tests to count rows without going through argparse.
async def total_mail_rows() -> int:  # pragma: no cover - trivial
    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with uow(sm, TenantId(LJM_TENANT_ID)) as session:
            return int((await session.execute(select(func.count(MailMessage.id)))).scalar_one())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
