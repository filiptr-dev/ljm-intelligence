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
) -> list[BrokerPrediction]:
    stmt = select(BrokerPrediction).order_by(BrokerPrediction.health_score.desc())
    if broker_domain:
        stmt = stmt.where(BrokerPrediction.broker_domain == broker_domain.lower())
    stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


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
