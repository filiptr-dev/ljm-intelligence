"""Outreach service — auto-send decision loop as a plain function.

Replaces the `_auto_send_impl(request, body)` route-level helper so the
pipeline no longer needs the `_Req`/`_AppState` fake-request hack. The HTTP
route in `app/api/enrichment.py` is a thin shim that constructs the inputs
and calls `auto_send(...)`; the pipeline calls `auto_send(...)` directly.

Compliance rules match the prior implementation exactly:

  * `outreach_postal_address` must be non-empty (CAN-SPAM footer).
  * Every email ALWAYS gets the unsubscribe footer + List-Unsubscribe headers,
    built from the fixed public base URL (never the request host). If the
    link cannot be built (no secret), nothing sends: ``no_unsub_config``.
  * A contact in `suppression` (any reason) is skipped, not sent.
  * Only contacts at ``pipeline_status == settings.auto_outreach_status_filter`` qualify.
  * Daily cap counted from ``sent_log.sent_at`` today (UTC).
  * Nothing sends if ``auto_outreach_enabled`` is False.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import Settings
from app.models import Lead, LeadContact, SentLog, SettingsRow, Suppression
from app.pipeline.enrichment import mark_contact_contacted
from app.services.unsub_config import (
    build_unsub_link,
    effective_unsub,
    unsub_headers,
    with_unsub_footer,
)


@dataclass
class AutoSendResult:
    status: str
    sent: int
    skipped_suppressed: int
    skipped_cap: int
    dry_run: bool


def _within_window(now_hour: int, start_h: int, end_h: int) -> bool:
    if start_h == end_h:
        return True
    if start_h < end_h:
        return start_h <= now_hour < end_h
    # window crosses midnight (e.g. 22..6)
    return now_hour >= start_h or now_hour < end_h


async def auto_send(
    *,
    sessionmaker,
    settings: Settings,
    dry_run: bool = False,
    sender=None,
    now_hour_override: int | None = None,
) -> AutoSendResult:
    """Decision + send loop. See module docstring for the compliance rules.

    `sender` is `None` in production (we only persist a `SentLog` row today);
    tests pass a mock that captures the outbound args.
    """
    now = datetime.now(UTC)

    async with sessionmaker() as s:
        cfg = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if cfg is None or not cfg.auto_outreach_enabled:
        return AutoSendResult("disabled", 0, 0, 0, dry_run)
    if not (settings.outreach_postal_address or "").strip():
        return AutoSendResult("no_footer", 0, 0, 0, dry_run)
    if cfg.auto_outreach_template_id is None:
        return AutoSendResult("no_template", 0, 0, 0, dry_run)

    unsub_secret, unsub_base = effective_unsub(settings, cfg)
    if not unsub_secret or not unsub_base:
        return AutoSendResult("no_unsub_config", 0, 0, 0, dry_run)

    now_hour = now_hour_override if now_hour_override is not None else now.hour
    if not _within_window(now_hour, cfg.auto_outreach_window_start_h, cfg.auto_outreach_window_end_h):
        return AutoSendResult("outside_window", 0, 0, 0, dry_run)

    async with sessionmaker() as s:
        today_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        sent_today = (
            await s.execute(select(func.count()).select_from(SentLog).where(SentLog.sent_at >= today_start))
        ).scalar_one()
        remaining = max(0, cfg.auto_outreach_daily_cap - int(sent_today or 0))
        if remaining == 0:
            return AutoSendResult("ok", 0, 0, 0, dry_run)

        stmt = (
            select(LeadContact)
            .join(Lead, Lead.id == LeadContact.lead_id)
            .where(LeadContact.pipeline_status == cfg.auto_outreach_status_filter)
            .where(LeadContact.email.is_not(None))
            .where(Lead.fit_score.is_not(None))
            .where(Lead.fit_score >= cfg.auto_outreach_min_fit)
            .order_by(Lead.fit_score.desc(), LeadContact.id)
        )
        candidates = (await s.execute(stmt)).scalars().all()

        supp = (await s.execute(select(Suppression.email))).scalars().all()
        suppressed = {e.lower() for e in supp if e}

        sent = 0
        skipped_supp = 0
        skipped_cap = 0
        for c in candidates:
            email = (c.email or "").lower()
            if not email or email in suppressed:
                skipped_supp += 1
                continue
            if sent >= remaining:
                skipped_cap += 1
                continue

            subject = "Trucking capacity"
            unsub = build_unsub_link(unsub_secret, unsub_base, c.id)
            body_text = with_unsub_footer(
                "Hello,\n\nLJM International runs dry vans across the eastern US. "
                "Reply if you have freight moving in the next couple of weeks.",
                postal_address=settings.outreach_postal_address,
                unsub_url=unsub,
            )
            headers = unsub_headers(unsub)

            if not dry_run:
                if sender is not None:
                    await sender(email, subject, body_text, headers=headers)
                s.add(
                    SentLog(
                        lead_id=c.lead_id,
                        template_id=cfg.auto_outreach_template_id,
                        mode="real" if not settings.simulated_delivery else "simulated",
                        to_email=email,
                        subject=subject,
                        body=body_text,
                        contact_id=c.id,
                    )
                )
                await mark_contact_contacted(s, c.id, now=now)
            sent += 1

        if not dry_run:
            await s.commit()
        return AutoSendResult(
            status="ok", sent=sent, skipped_suppressed=skipped_supp, skipped_cap=skipped_cap, dry_run=dry_run
        )
