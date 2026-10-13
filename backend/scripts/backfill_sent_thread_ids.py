"""One-off: re-key real sent mails stored under a synthetic ``t-…`` thread id
to Gmail's real ``threadId``, so recipient replies group with our sent email.

For every ``mail_messages`` row with ``thread_id LIKE 't-%'`` that was a real
send (not ``local-…`` / simulated), ask Gmail (``messages.get`` by message_id,
impersonating the row's mailbox) for its ``threadId`` and update the message
and its ``no_reply_tracker`` row.

Run inside the api container::

    docker exec ljm-local-api-1 \
        bash -lc "PYTHONPATH=/app python /app/scripts/backfill_sent_thread_ids.py [--dry-run]"

Idempotent: once re-keyed a row no longer matches ``t-%``.
"""
from __future__ import annotations

import asyncio
import sys

import anyio
from sqlalchemy import select

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.inbox.models import MailMessage, NoReplyTracker
from app.integrations.adapters.email.mailbox import GmailMailbox
from app.shared.db import uow
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId


async def _main(dry_run: bool) -> None:
    settings = get_settings()
    mailbox_client = GmailMailbox(settings)
    services: dict[str, object] = {}

    def _gmail_thread_id(mailbox: str, message_id: str) -> str | None:
        svc = services.get(mailbox)
        if svc is None:
            svc = services[mailbox] = mailbox_client._service(mailbox)
        if svc is None:
            return None
        resp = (
            svc.users()  # type: ignore[attr-defined]
            .messages()
            .get(userId="me", id=message_id, format="minimal")
            .execute()
        )
        return resp.get("threadId")

    engine = create_engine(settings)
    updated = skipped = failed = 0
    try:
        sm = create_sessionmaker(engine)
        async with uow(sm, TenantId(LJM_TENANT_ID)) as session:
            rows = (
                await session.execute(
                    select(MailMessage).where(
                        MailMessage.thread_id.like("t-%"),
                        ~MailMessage.message_id.like("local-%"),
                    )
                )
            ).scalars().all()
            for m in rows:
                if (m.raw or {}).get("simulated"):
                    skipped += 1
                    continue
                old = m.thread_id
                try:
                    new = await anyio.to_thread.run_sync(
                        _gmail_thread_id, m.mailbox, m.message_id
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"FAIL {m.mailbox} {m.message_id}: {exc}")
                    failed += 1
                    continue
                if not new or new == old:
                    skipped += 1
                    continue
                print(f"{m.mailbox} {m.message_id}: {old} -> {new}")
                if dry_run:
                    continue
                m.thread_id = new
                trackers = (
                    await session.execute(
                        select(NoReplyTracker).where(
                            NoReplyTracker.mailbox == m.mailbox,
                            NoReplyTracker.thread_id == old,
                        )
                    )
                ).scalars().all()
                clash = (
                    await session.execute(
                        select(NoReplyTracker).where(
                            NoReplyTracker.mailbox == m.mailbox,
                            NoReplyTracker.thread_id == new,
                        )
                    )
                ).scalars().first()
                for t in trackers:
                    if clash is not None:
                        await session.delete(t)  # unique (mailbox, thread_id)
                    else:
                        t.thread_id = new
                updated += 1
            if dry_run:
                await session.rollback()
    finally:
        await engine.dispose()
    print(f"updated={updated} skipped={skipped} failed={failed} dry_run={dry_run}")


if __name__ == "__main__":
    asyncio.run(_main("--dry-run" in sys.argv))
