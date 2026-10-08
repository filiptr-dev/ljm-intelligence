"""Outreach module jobs — enrichment auto-send (CAN-SPAM compliant).

Idempotency: `app.outreach.service.auto_send` already applies the daily
cap and suppression check — a repeat tick in the same window is a no-op.
"""

from __future__ import annotations

import logging

from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


@app.task(name="outreach.auto_send", queue="default", pass_context=False)
async def enrichment_auto_send(tenant_id: str, dry_run: bool = False) -> None:
    set_tenant(TenantId(tenant_id))
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.outreach.service import auto_send

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        result = await auto_send(sessionmaker=sm, settings=settings, dry_run=dry_run)
        log.info(
            "outreach.auto_send: done",
            extra={"status": result.status, "sent": result.sent, "tenant_id": tenant_id},
        )
    finally:
        await engine.dispose()


@app.task(name="outreach.send_to_contact", queue="default", pass_context=False)
async def send_to_contact(contact_id: int, tone: str = "professional") -> None:
    """Campaign fan-out worker — one job per freight-manager contact.

    Looks up the contact, drafts (reusing the composer-by-contact path), and
    sends via the configured mail sender (DB override > env). Suppression
    and per-send rate-limit stay in the service layer we already use.
    """
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.outreach.email_service import draft as svc_draft, send as svc_send

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        try:
            d = await svc_draft(
                sm, settings,
                lead_id=None, lead_payload=None, contact_id=contact_id,
                tone=tone, instructions=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("send_to_contact: draft failed for %s: %s", contact_id, exc)
            return
        from app.outreach import repository as repo

        async with sm() as s:
            c = await repo.get_contact(s, contact_id)
            if c is None or not c.email:
                log.info("send_to_contact: contact %s missing or no email", contact_id)
                return
            to_addr = c.email
            lead_id = c.lead_id
            if await repo.contact_already_sent(s, contact_id, d.subject):
                log.info("send_to_contact: contact %s already sent this campaign, skipping", contact_id)
                return
        result = await svc_send(
            sm, settings,
            to=to_addr, subject=d.subject, body=d.body, body_html=d.body_html,
            lead_id=lead_id, contact_id=contact_id,
            in_reply_to=None, references=None, thread_id=None,
        )
        log.info("send_to_contact: contact=%s mode=%s ok=%s", contact_id, result.mode, result.ok)
    finally:
        await engine.dispose()
