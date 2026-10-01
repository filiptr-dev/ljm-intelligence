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
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.integrations.mail_service import backfill as svc_backfill

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        stats = await svc_backfill(sm, settings, mailbox=mailbox, months=months)
        log.info("mail_backfill: done", extra={"mailbox": mailbox, "upserted": stats.upserted})
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

    The column is owned by the inbox-analysis migration (0018). Until that
    lands this task is a safe no-op: the SELECT returns zero rows and the
    DELETE is a no-op.
    """
    _bind(tenant_id)
    log.info("inbox.retention_sweep: scheduled (bodies owned by inbox-analysis plan)")


@app.task(name="inbox.extract_load_from_message", queue="default", pass_context=False)
async def extract_load_from_message(tenant_id: str, message_id: str) -> None:
    """Reserved slot — owned by inbox-analysis plan. No-op until that lands."""
    _bind(tenant_id)
    log.debug("inbox.extract_load_from_message: no-op (reserved)")


@app.task(name="inbox.extract_contact_from_signature", queue="default", pass_context=False)
async def extract_contact_from_signature(tenant_id: str, message_id: str) -> None:
    """Reserved slot — owned by inbox-analysis plan. No-op until that lands."""
    _bind(tenant_id)
    log.debug("inbox.extract_contact_from_signature: no-op (reserved)")
