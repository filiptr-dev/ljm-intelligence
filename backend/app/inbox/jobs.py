"""Inbox module jobs — mail backfill, incremental sync, retention sweep.

The ``extract_*`` fan-out tasks (``extract_load_from_message``,
``extract_contact_from_signature``) are reserved slots owned by
[[ljm-intelligence-inbox-analysis-gmail-connector-plan]]; this file
declares them as pass-through placeholders so the scheduler and queue
registry are shaped for day 1 of the inbox-analysis build. The analysis
plan swaps the bodies without touching this file's shape.

Idempotency:
  * ``mail_backfill`` — Gmail `historyId` cursor; a second run picks up
    where the first stopped.
  * ``mail_incremental`` — same cursor; empty page = clean no-op.
  * ``retention_sweep`` — delete-by-`retention_until`, naturally idempotent.
"""

from __future__ import annotations

import logging

from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


def _bind(tenant_id: str) -> None:
    set_tenant(TenantId(tenant_id))


@app.task(name="inbox.mail_backfill", queue="default", pass_context=False)
async def mail_backfill(tenant_id: str, mailbox: str, months: int = 12) -> None:
    _bind(tenant_id)
    from sqlalchemy import select

    from app.analysis.models import PredictionRun
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.integrations.mail_service import backfill as svc_backfill
    from app.shared.queue import dispatch

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        stats = await svc_backfill(sm, settings, mailbox=mailbox, months=months)
        log.info("mail_backfill: done", extra={"mailbox": mailbox, "upserted": stats.upserted})

        # First-backfill → one-shot analysis trigger. Gate on PredictionRun so a
        # second mailbox's backfill (or any subsequent run) never re-queues it.
        # Belt + braces: nightly cron and `analysis.nightly` itself are already
        # re-run-safe (DELETE-then-INSERT per snapshot) — this gate is the
        # cleanest short-circuit and keeps the behavior obvious.
        async with sm() as session:
            existing = await session.execute(
                select(PredictionRun.id)
                .where(PredictionRun.tenant_id == tenant_id)
                .where(PredictionRun.kind == "analysis_nightly")
                .limit(1)
            )
            if existing.first() is None:
                await dispatch("analysis.nightly", tenant_id=tenant_id)
                log.info("mail_backfill: first-backfill analysis queued", extra={"tenant_id": tenant_id})
            else:
                log.info("mail_backfill: analysis already present, skip first-run trigger")
    finally:
        await engine.dispose()


@app.task(name="inbox.mail_incremental", queue="default", pass_context=False)
async def mail_incremental(tenant_id: str, mailbox: str | None = None) -> None:
    _bind(tenant_id)
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.integrations.mail_service import incremental as svc_incremental

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        result = await svc_incremental(sm, settings, mailbox=mailbox)
        log.info("mail_incremental: done", extra={"items": len(result.items)})
    finally:
        await engine.dispose()


@app.task(name="inbox.retention_sweep", queue="default", pass_context=False)
async def retention_sweep(tenant_id: str) -> None:
    """Nightly delete of `mail_messages` past `retention_until`.

    The column is stamped by ingest (``now() + 18 months`` default). The
    delete is naturally idempotent — running twice removes nothing extra.
    """
    _bind(tenant_id)
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.inbox.service import retention_sweep as svc_sweep

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with sm() as session:
            result = await svc_sweep(session)
            await session.commit()
        log.info("inbox.retention_sweep: done %s", result)
    finally:
        await engine.dispose()


@app.task(name="inbox.extract_load_from_message", queue="default", pass_context=False)
async def extract_load_from_message(tenant_id: str, message_id: str) -> None:
    """Parse a broker email body into zero-or-more loads via the AI seam.

    Owned by [[ljm-intelligence-loads-aggregator-headless-agent-plan]].

    * Loads the ``MailMessage`` by id and feeds its text through the
      ``inbox_analysis`` provider as JSON.
    * Each returned row flows through ``loads_service._store_batch``, which
      attaches a ``broker_lead_id`` via the standard email → phone →
      (name, origin_state) ladder and stamps the dedupe hash.
    * Source is ``inbox``, ``source_ref="msg:<id>"`` — the unique index on
      ``(source, source_ref)`` makes a second call a clean no-op.
    * NullProvider / missing key → zero rows, zero crash (Gemini optional).
    """
    _bind(tenant_id)
    from sqlalchemy import select

    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.inbox.models import MailMessage
    from app.integrations.adapters.ai import provider as ai_provider
    from app.integrations.loads_extract import extract_loads_from_text
    from app.integrations.loads_service import _store_batch

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with sm() as s:
            row = (
                await s.execute(select(MailMessage).where(MailMessage.id == message_id))
            ).scalar_one_or_none()
        if row is None:
            log.warning("inbox.extract_load: message %s not found", message_id)
            return
        text = (row.body_text or "")[:32000]
        if not text.strip():
            return
        provider = ai_provider.get_for("inbox_analysis", settings=settings)
        raws = await extract_loads_from_text(
            provider,
            text,
            source="inbox",
            source_ref=f"msg:{message_id}",
        )
        if not raws:
            return
        inserted, skipped = await _store_batch(sm, raws)
        log.info(
            "inbox.extract_load: %s inserted=%d skipped=%d",
            message_id, inserted, skipped,
        )
    finally:
        await engine.dispose()


@app.task(name="inbox.extract_contact_from_signature", queue="default", pass_context=False)
async def extract_contact_from_signature(tenant_id: str, message_id: str) -> None:
    """Reserved slot — owned by inbox-analysis plan. No-op until that lands."""
    _bind(tenant_id)
    log.debug("inbox.extract_contact_from_signature: no-op (reserved)")
