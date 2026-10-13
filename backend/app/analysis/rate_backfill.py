"""Backfill derived lane / rate / order / pickup / equipment on existing mail.

Thin orchestration over :mod:`app.inbox.load_extract`:

1. Scan every ``message_insights`` row where ``derived_source IS NULL``
   AND ``intent`` is in ``LOAD_INTENTS``. Join to ``mail_messages`` for
   subject + body (quoted-reply text is stripped inside the extractor).
2. Run the deterministic extractor. Keep what it found.
3. If still no lane AND an AI provider is wired, run the AI fallback
   (per-run cap, default 200). Null provider → skip.
4. Write the result to ``message_insights.derived_*`` and mirror into
   the matching ``loads(source='inbox')`` row — creating one when no
   row exists but a lane/order was recovered. The ``dedupe_group_hash``
   is recomputed so unknown-lane rows stop collapsing into "? → ?".

Idempotent: the WHERE clause gates on ``derived_source IS NULL`` so a
re-run writes zero new rows.

Run:
    curl -X POST -H "Authorization: Bearer <token>" \
         localhost:8000/analysis/jobs/backfill-subject-rates

CLI:
    python -m app.analysis.rate_backfill --tenant <tid>
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.load_extract import (
    LOAD_INTENTS,
    ExtractedLoad,
    ai_extract_from_message,
    extract_from_message,
)
from app.inbox.models import MailMessage, MessageInsight
from app.prospecting.models import Load

log = logging.getLogger(__name__)

AI_PER_RUN_CAP = 200


@dataclass
class BackfillStats:
    insights_scanned: int = 0
    insights_filled: int = 0
    insights_filled_by_ai: int = 0
    loads_created: int = 0
    loads_updated: int = 0
    loads_rehashed: int = 0


def _group_hash(
    *,
    broker_name: str | None,
    origin_state: str | None,
    dest_state: str | None,
    pickup_date: Any,
    equipment: str | None,
    source: str,
    source_ref: str,
) -> str:
    """Mirror ``loads_service._group_hash``, with a null-lane escape hatch.

    When both ``origin_state`` and ``dest_state`` are missing, group by
    ``source|source_ref`` instead so unknown-lane inbox rows don't all
    collapse into one hash of empty strings on /loads.
    """
    if not origin_state and not dest_state:
        key = f"__nolane__|{source}|{source_ref}"
    else:
        pickup = ""
        if pickup_date is not None:
            pickup = str(pickup_date)[:10]
        key = "|".join(
            [
                (broker_name or "").strip().lower(),
                (origin_state or "").strip().lower(),
                (dest_state or "").strip().lower(),
                pickup,
                (equipment or "").strip().lower(),
            ]
        )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _apply_to_insight(ins: MessageInsight, ex: ExtractedLoad) -> None:
    ins.derived_rate_usd = ex.rate_usd
    ins.derived_lane_from = (
        f"{ex.origin_city}, {ex.origin_state}" if ex.origin_city and ex.origin_state else None
    )
    ins.derived_lane_to = (
        f"{ex.dest_city}, {ex.dest_state}" if ex.dest_city and ex.dest_state else None
    )
    ins.derived_order_no = ex.order_no
    ins.derived_source = ex.source


async def _upsert_load_from_extracted(
    session: AsyncSession,
    *,
    mailbox: str,
    message_id: str,
    broker_name: str | None,
    broker_email: str | None,
    sent_at: datetime | None,
    ex: ExtractedLoad,
) -> tuple[bool, bool]:
    """Create or update a ``loads(source='inbox')`` row from an extraction.

    Returns ``(created, updated_or_rehashed)``.
    """
    source_ref = f"{mailbox}:{message_id}"
    existing = (
        await session.execute(
            select(Load).where(Load.source == "inbox", Load.source_ref == source_ref)
        )
    ).scalar_one_or_none()

    if existing is None and ex.has_lane:
        # Create a load from the extraction. Only when we actually have
        # a lane — a bare rate with no lane isn't a useful /loads card.
        from app.prospecting.broker_matching import match_broker_lead

        broker_lead_id = await match_broker_lead(
            session, email=broker_email, name=broker_name
        )
        gh = _group_hash(
            broker_name=broker_name,
            origin_state=ex.origin_state,
            dest_state=ex.dest_state,
            pickup_date=ex.pickup_date,
            equipment=ex.equipment,
            source="inbox",
            source_ref=source_ref,
        )
        session.add(
            Load(
                source="inbox",
                source_ref=source_ref,
                broker_name=broker_name or "",
                broker_email=broker_email,
                origin_city=None,
                origin_state=None,
                dest_city=None,
                dest_state=None,
                pickup_date=None,
                equipment=ex.equipment,
                rate_usd=None,
                posted_at=sent_at,
                raw={"message_id": message_id, "mailbox": mailbox, "derived": True},
                broker_lead_id=broker_lead_id,
                dedupe_group_hash=gh,
                derived_rate_usd=ex.rate_usd,
                derived_origin=(
                    f"{ex.origin_city}, {ex.origin_state}"
                    if ex.origin_city and ex.origin_state
                    else None
                ),
                derived_dest=(
                    f"{ex.dest_city}, {ex.dest_state}"
                    if ex.dest_city and ex.dest_state
                    else None
                ),
            )
        )
        return True, False

    if existing is None:
        return False, False

    changed = False
    if existing.derived_rate_usd is None and ex.rate_usd is not None:
        existing.derived_rate_usd = ex.rate_usd
        changed = True
    if existing.derived_origin is None and ex.origin_city and ex.origin_state:
        existing.derived_origin = f"{ex.origin_city}, {ex.origin_state}"
        changed = True
    if existing.derived_dest is None and ex.dest_city and ex.dest_state:
        existing.derived_dest = f"{ex.dest_city}, {ex.dest_state}"
        changed = True

    # Rehash: authoritative state wins; derived state fills in; still
    # unknown → null-lane escape hatch. Idempotent — a correctly-hashed
    # row recomputes to the same value and ``changed`` stays what it was.
    origin_state = existing.origin_state or ex.origin_state
    dest_state = existing.dest_state or ex.dest_state
    new_hash = _group_hash(
        broker_name=existing.broker_name,
        origin_state=origin_state,
        dest_state=dest_state,
        pickup_date=existing.pickup_date,
        equipment=existing.equipment or ex.equipment,
        source=existing.source,
        source_ref=existing.source_ref,
    )
    if new_hash != existing.dedupe_group_hash:
        existing.dedupe_group_hash = new_hash
        changed = True
    return False, changed


async def backfill_subject_rates(
    session: AsyncSession,
    *,
    provider: Any = None,
    ai_cap: int = AI_PER_RUN_CAP,
    ai_min_interval_s: float = 2.0,
) -> BackfillStats:
    """Fill ``derived_*`` on every eligible row in one pass.

    * Eligibility: ``derived_source IS NULL`` AND ``intent IN LOAD_INTENTS``.
    * AI fallback: only when the deterministic pass misses the lane AND
      a provider was passed in. Capped at ``ai_cap`` calls per run AND
      throttled to one call every ``ai_min_interval_s`` seconds. The whole
      AI loop stops on the first ``429`` / rate-limit signal so the live
      app's AI budget is never starved by this maintenance job.
    """
    import asyncio
    import time
    stats = BackfillStats()
    rows = (
        await session.execute(
            select(MessageInsight, MailMessage)
            .join(
                MailMessage,
                and_(
                    MailMessage.mailbox == MessageInsight.mailbox,
                    MailMessage.message_id == MessageInsight.message_id,
                ),
            )
            .where(MessageInsight.derived_source.is_(None))
            .where(MessageInsight.intent.in_(sorted(LOAD_INTENTS)))
        )
    ).all()
    stats.insights_scanned = len(rows)

    ai_calls = 0
    ai_disabled = False
    last_ai_at = 0.0
    for ins, m in rows:
        ex = extract_from_message(m.subject, m.body_text)
        if (
            not ex.has_lane
            and provider is not None
            and not ai_disabled
            and ai_calls < ai_cap
            and (ins.intent in LOAD_INTENTS)
        ):
            # Throttle: ≥ ai_min_interval_s between calls.
            now = time.monotonic()
            wait = ai_min_interval_s - (now - last_ai_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                ai_ex = await ai_extract_from_message(provider, m.subject, m.body_text)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "429" in msg or "rate" in msg or "quota" in msg:
                    log.warning("rate-backfill: AI 429/quota — stopping AI loop for the rest of this run")
                    ai_disabled = True
                    continue
                raise
            ai_calls += 1
            last_ai_at = time.monotonic()
            if ai_ex is not None and (ai_ex.has_lane or ai_ex.rate_usd is not None):
                ex = ai_ex
                stats.insights_filled_by_ai += 1
        if ex.is_empty:
            continue
        _apply_to_insight(ins, ex)
        stats.insights_filled += 1
        created, changed = await _upsert_load_from_extracted(
            session,
            mailbox=m.mailbox,
            message_id=m.message_id,
            broker_name=ins.broker_name or (m.from_addr or "").split("<")[0].strip() or None,
            broker_email=(m.email_lower or m.from_addr or None),
            sent_at=m.sent_at,
            ex=ex,
        )
        if created:
            stats.loads_created += 1
        if changed:
            stats.loads_updated += 1

    # Second pass: every inbox load whose lane is still unknown and whose
    # dedupe_group_hash was the empty-lane hash gets its hash recomputed
    # to the per-source_ref escape hatch so /loads stops collapsing them.
    unknown_lane_loads = (
        await session.execute(
            select(Load)
            .where(Load.source == "inbox")
            .where(Load.origin_state.is_(None))
            .where(Load.dest_state.is_(None))
        )
    ).scalars().all()
    for load in unknown_lane_loads:
        new_hash = _group_hash(
            broker_name=load.broker_name,
            origin_state=None,
            dest_state=None,
            pickup_date=load.pickup_date,
            equipment=load.equipment,
            source=load.source,
            source_ref=load.source_ref,
        )
        if new_hash != load.dedupe_group_hash:
            load.dedupe_group_hash = new_hash
            stats.loads_rehashed += 1

    await session.commit()
    log.info(
        "rate-backfill: scanned=%d filled=%d (ai=%d) loads_created=%d updated=%d rehashed=%d",
        stats.insights_scanned,
        stats.insights_filled,
        stats.insights_filled_by_ai,
        stats.loads_created,
        stats.loads_updated,
        stats.loads_rehashed,
    )
    return stats


# ---- CLI entry point ------------------------------------------------------


async def _run_cli(tenant_id: str | None, *, with_ai: bool = False) -> None:
    """``python -m app.analysis.rate_backfill`` entry point.

    Default: deterministic-only (no provider passed). ``--with-ai`` opts the
    run back into the AI fallback with the throttle + 429 kill-switch active.
    The default was flipped after a background run of this job starved the
    live Ask / draft paths of their AI budget (Gemini 429'd for hours).
    """
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    sm = create_sessionmaker(engine)
    provider: Any = None
    if with_ai:
        try:
            from app.integrations.adapters.ai.provider import get_for

            provider = get_for("inbox_draft_reply", settings=settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("rate-backfill: provider resolve failed, running deterministic-only: %s", exc)
            provider = None
    try:
        async with sm() as session:
            stats = await backfill_subject_rates(session, provider=provider)
        print(
            f"rate-backfill ok: scanned={stats.insights_scanned} "
            f"filled={stats.insights_filled} ai={stats.insights_filled_by_ai} "
            f"loads_created={stats.loads_created} updated={stats.loads_updated} "
            f"rehashed={stats.loads_rehashed}"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Backfill derived rate/lane/order from mail subjects + body."
    )
    parser.add_argument("--tenant", default=None, help="Tenant id (default: 'default')")
    parser.add_argument(
        "--with-ai",
        action="store_true",
        help="Enable AI fallback for lane extraction (throttled, 429-stop). "
             "Default off so the live app's AI budget is never starved.",
    )
    args = parser.parse_args()
    asyncio.run(_run_cli(args.tenant, with_ai=args.with_ai))
