"""Analysis — repository seam.

All SELECT calls for analysis-owned aggregates (BrokerPrediction,
LanePrediction, BrokerLookalike, ObjectionCluster) plus cross-module
read helpers against inbox tables (MailMessage, MessageInsight). The
cross-module reads are kept here (the `Queries/Public` pattern) because
analysis is read-only against inbox — writes still belong to inbox.
See: projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md.
"""
from __future__ import annotations

from typing import Any, Protocol  # noqa: F401

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import (
    BrokerLookalike,
    BrokerPrediction,
    LanePrediction,
    ObjectionCluster,
)
from app.inbox.models import MailMessage, MessageInsight
from app.prospecting.models import Lead, LeadContact

# Domains that must never surface as a "broker" in the UI, regardless of
# where a BrokerPrediction row came from. Vendor + our own + free-mail.
_EXCLUDE_DOMAINS = {
    "ljminternational.com", "loadstop.com", "highway.com",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "proton.me", "protonmail.com",
    "msn.com", "live.com", "googlemail.com", "ymail.com",
}

# Freight-intent signals that make a domain "worth showing" even when no
# Lead row has that domain yet (the leads.domain backfill is a separate
# task; this read-side rule keeps the UI honest in the meantime).
_FREIGHT_INTENTS = ("booked", "load_offer", "rate_request", "urgent_truck")


async def fetch_messages_for_corpus(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                select(
                    MailMessage.message_id,
                    MailMessage.mailbox,
                    MailMessage.thread_id,
                    MailMessage.from_addr,
                    MailMessage.to_addrs,
                    MailMessage.subject,
                    MailMessage.body_text,
                    MailMessage.sent_at,
                    MailMessage.email_lower,
                )
                .order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
            )
        ).all()
    )


async def fetch_insights_for_corpus(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                select(
                    MessageInsight.mailbox,
                    MessageInsight.message_id,
                    MessageInsight.thread_id,
                    MessageInsight.intent,
                    MessageInsight.urgency,
                    MessageInsight.sentiment,
                    MessageInsight.broker_name,
                    MessageInsight.rate_usd,
                    MessageInsight.lane_from,
                    MessageInsight.lane_to,
                    MessageInsight.equipment,
                    MessageInsight.evidence,
                    MessageInsight.from_email_normalized,
                )
            )
        ).all()
    )


async def list_broker_predictions_rows(
    session: AsyncSession, *, broker_domain: str | None, limit: int
) -> list[tuple[BrokerPrediction, str | None]]:
    """Return (prediction, lead_id) pairs filtered to real Broker leads.

    The nightly fan-out walks every inbound domain (correctly — we don't
    want to re-shape ingest). The whitelist happens here on the read seam
    so wexinc.com / coxautoinc.com / remotelyplushire.com never reach the
    UI, and if ops later points a Lead at a domain nothing in the write
    path has to change. We expose ``lead_id`` alongside so each insight
    card can link to ``/brokers/[id]``.
    """
    # Build the allowed-domain set in three parts, ORM-only:
    #  (a) non-free domains attached to a Broker Lead's primary_email or
    #      domain or any lead_contacts.email;
    #  (b) domains that have freight-intent mail in message_insights;
    # minus the vendor/own/free exclusion list.
    #
    # The previous rule required a direct ``Lead.domain = broker_domain``
    # join, which collapses to 1 of 216 rows today because leads.domain is
    # empty for 9500 of 9501 rows. The P0 fix keeps the UI honest while a
    # separate leads.domain backfill runs.
    allowed: set[str] = set()

    # (a) Lead-side domains — primary_email domain + leads.domain + contacts.
    for row in (
        await session.execute(
            select(Lead.id, Lead.domain, Lead.primary_email).where(Lead.kind == "Broker")
        )
    ).all():
        _, dom, pe = row
        if dom:
            allowed.add(dom.strip().lower())
        if pe and "@" in pe:
            allowed.add(pe.rsplit("@", 1)[1].strip().lower())
    for row in (
        await session.execute(select(LeadContact.email))
    ).all():
        em = row[0]
        if em and "@" in em:
            allowed.add(em.rsplit("@", 1)[1].strip().lower())

    # (b) domains with freight-intent mail.
    for row in (
        await session.execute(
            select(func.lower(MailMessage.email_domain))
            .join(
                MessageInsight,
                (MessageInsight.mailbox == MailMessage.mailbox)
                & (MessageInsight.message_id == MailMessage.message_id),
            )
            .where(MessageInsight.intent.in_(_FREIGHT_INTENTS))
            .where(MailMessage.email_domain.isnot(None))
            .distinct()
        )
    ).all():
        dom = row[0]
        if dom:
            allowed.add(dom.strip())

    # Strip vendor / own / free.
    allowed = {d for d in allowed if d and d not in _EXCLUDE_DOMAINS}

    # Build a reverse map broker_domain → lead_id (best effort — prefer
    # leads.domain exact match, else fall back to primary_email domain,
    # else lead_contacts.email domain). First hit wins.
    lead_by_domain: dict[str, str] = {}
    for row in (
        await session.execute(
            select(Lead.id, Lead.domain, Lead.primary_email).where(Lead.kind == "Broker")
        )
    ).all():
        lid, dom, pe = row
        if dom:
            lead_by_domain.setdefault(dom.strip().lower(), lid)
        if pe and "@" in pe:
            lead_by_domain.setdefault(pe.rsplit("@", 1)[1].strip().lower(), lid)
    for row in (
        await session.execute(select(LeadContact.email, LeadContact.lead_id))
    ).all():
        em, lid = row
        if em and "@" in em and lid:
            lead_by_domain.setdefault(em.rsplit("@", 1)[1].strip().lower(), lid)

    stmt = select(BrokerPrediction).order_by(BrokerPrediction.health_score.desc())
    if broker_domain:
        stmt = stmt.where(BrokerPrediction.broker_domain == broker_domain.lower())
    stmt = stmt.limit(limit * 3 if not broker_domain else limit)
    rows = (await session.execute(stmt)).scalars().all()

    out: list[tuple[BrokerPrediction, str | None]] = []
    for p in rows:
        dom = (p.broker_domain or "").strip().lower()
        if not dom:
            continue
        # ``*.mail.parade.ai`` relays — treat the preceding label as the
        # real broker (display still shows the resolved name in service).
        effective = dom
        if dom.endswith(".mail.parade.ai"):
            effective = dom.rsplit(".mail.parade.ai", 1)[0].split(".")[-1] + ".com"
        if dom in _EXCLUDE_DOMAINS or effective in _EXCLUDE_DOMAINS:
            continue
        if dom not in allowed and effective not in allowed:
            continue
        lid = lead_by_domain.get(dom) or lead_by_domain.get(effective)
        out.append((p, lid))
        if len(out) >= limit:
            break
    return out


async def list_lane_predictions_rows(
    session: AsyncSession, *, origin: str | None, dest: str | None,
    equipment: str | None, limit: int,
) -> list[LanePrediction]:
    stmt = select(LanePrediction).order_by(LanePrediction.sample_size.desc())
    if origin:
        stmt = stmt.where(LanePrediction.origin.ilike(origin))
    if dest:
        stmt = stmt.where(LanePrediction.dest.ilike(dest))
    if equipment:
        stmt = stmt.where(LanePrediction.equipment == equipment)
    stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def list_lookalikes_rows(
    session: AsyncSession, *, broker_domain: str, top_n: int
) -> list[BrokerLookalike]:
    rows = (
        await session.execute(
            select(BrokerLookalike)
            .where(BrokerLookalike.broker_domain == broker_domain.lower())
            .order_by(BrokerLookalike.score.desc())
            .limit(top_n)
        )
    ).scalars().all()
    return list(rows)


async def list_objections_rows(
    session: AsyncSession, *, broker_domain: str | None, limit: int
) -> list[ObjectionCluster]:
    stmt = select(ObjectionCluster).order_by(ObjectionCluster.count.desc())
    if broker_domain:
        stmt = stmt.where(ObjectionCluster.broker_domain == broker_domain.lower())
    stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def fetch_workload_rows(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                select(MailMessage.sent_at, MailMessage.from_addr, MailMessage.mailbox)
            )
        ).all()
    )


async def fetch_thread_age_messages(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                select(
                    MailMessage.thread_id, MailMessage.from_addr, MailMessage.mailbox,
                    MailMessage.sent_at, MailMessage.mailbox, MailMessage.message_id,
                ).order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
            )
        ).all()
    )


async def fetch_thread_age_intents(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                select(MessageInsight.mailbox, MessageInsight.message_id, MessageInsight.intent)
            )
        ).all()
    )


async def fetch_loss_reason_counts(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                select(ObjectionCluster.label, func.sum(ObjectionCluster.count))
                .group_by(ObjectionCluster.label)
                .order_by(func.sum(ObjectionCluster.count).desc())
            )
        ).all()
    )


async def fetch_first_touch_rows(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                select(
                    BrokerPrediction.broker_domain, BrokerPrediction.first_touch_latency_days,
                    BrokerPrediction.computed_at,
                )
                .where(BrokerPrediction.first_touch_latency_days.isnot(None))
                .order_by(BrokerPrediction.first_touch_latency_days.asc())
            )
        ).all()
    )


# ---- writes (DIP: writes still go through the repo seam) ------------------

from sqlalchemy import delete as _delete


async def wipe_prediction_tables(session: AsyncSession) -> None:
    """Delete every row across the four prediction tables. Used by the
    nightly recompute job before fresh writes."""
    await session.execute(_delete(BrokerPrediction))
    await session.execute(_delete(LanePrediction))
    await session.execute(_delete(BrokerLookalike))
    await session.execute(_delete(ObjectionCluster))
