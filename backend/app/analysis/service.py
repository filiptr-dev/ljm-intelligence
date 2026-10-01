"""Analysis service — predictions + further analyses.

Everything in here is deterministic / feature-weighted (no trained ML yet).
Reads from inbox data (``mail_messages`` + ``message_insights``) and writes
to prediction tables (``broker_predictions``, ``lane_predictions``,
``broker_lookalikes``, ``objection_clusters``). The nightly job in
``analysis.jobs`` is the only writer — HTTP endpoints read-only.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import (
    BrokerLookalike,
    BrokerPrediction,
    LanePrediction,
    ObjectionCluster,
    PredictionRun,
)
from app.inbox.models import MailMessage, MessageInsight


# ---- shapes ---------------------------------------------------------------


@dataclass
class BrokerPredictionRow:
    broker_domain: str
    broker_name: str | None
    win_probability: float
    health_score: int
    best_send_hour: int | None
    is_slow_payer: bool
    churn_risk: float
    reply_speed_lift: float
    first_touch_latency_days: float | None
    computed_at: datetime


@dataclass
class LanePredictionRow:
    origin: str
    dest: str
    equipment: str | None
    price_p50: float | None
    price_p75: float | None
    price_p90: float | None
    season_hint: dict
    sample_size: int
    computed_at: datetime


@dataclass
class LookalikeRow:
    broker_domain: str
    peer_domain: str
    score: float


@dataclass
class ObjectionRow:
    broker_domain: str
    label: str
    count: int
    exemplar: str | None


@dataclass
class WorkloadCell:
    hour: int
    day: int  # 0=Mon
    count: int


@dataclass
class ThreadAgeBucket:
    intent: str
    median_minutes: int
    p90_minutes: int
    count: int


@dataclass
class LossReasonRow:
    reason: str
    count: int


@dataclass
class FirstTouchRow:
    broker_domain: str
    first_touch_at: datetime | None
    first_load_at: datetime | None
    latency_days: float | None


# ---- helpers --------------------------------------------------------------


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.strip().lower().split("@", 1)[1]


def _percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return float(xs[k])


# ---- compute (writers) ---------------------------------------------------


async def _collect_corpus(session: AsyncSession) -> tuple[list[Any], list[Any]]:
    """Pull (messages, insights) once; downstream analyses walk the lists."""
    msgs = (
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
    ins = (
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
    return msgs, ins


def _compute_broker_predictions(msgs: list[Any], ins: list[Any]) -> list[BrokerPredictionRow]:
    """Deterministic feature-weighted scoring per broker domain.

    Features in play:
      - win_probability: rate of booked/praise insights over the broker's inbound total.
      - health_score: 50 base + sentiment*20 + praise*3 - complaint*5 - silence
      - best_send_hour: hour-of-day with the highest ratio of our outbounds that got a reply.
      - is_slow_payer: >=2 insights with intent=='payment'.
      - churn_risk: 0..1 based on volume drop in last 30d vs prior 30d.
      - reply_speed_lift: ratio of win rate for threads with fast-reply (<60min) vs slow (>=60min).
      - first_touch_latency_days: days from first inbound to first load_offer insight.
    """
    now = datetime.now(UTC)
    # Index messages by (mailbox, message_id) for insight lookup.
    msg_by_pk = {(m.mailbox, m.message_id): m for m in msgs}
    # Group everything by broker_domain.
    by_dom: dict[str, dict] = defaultdict(lambda: {
        "name": None,
        "insights": [],
        "inbound": [],  # msg rows
        "outbound_hours": Counter(),  # hour -> replied count
        "outbound_total_hours": Counter(),
        "intents": Counter(),
        "sentiments": [],
        "first_inbound": None,
        "first_load_offer": None,
        "last_30d_in": 0,
        "prior_30d_in": 0,
        "fast_wins": 0,
        "fast_losses": 0,
        "slow_wins": 0,
        "slow_losses": 0,
    })

    for m in msgs:
        dom = _domain(m.email_lower or m.from_addr)
        if not dom or m.from_addr == m.mailbox:
            continue
        bucket = by_dom[dom]
        bucket["inbound"].append(m)
        t = _as_utc(m.sent_at)
        if t:
            if bucket["first_inbound"] is None or t < bucket["first_inbound"]:
                bucket["first_inbound"] = t
            days_ago = (now - t).days
            if days_ago <= 30:
                bucket["last_30d_in"] += 1
            elif days_ago <= 60:
                bucket["prior_30d_in"] += 1

    for i in ins:
        pk = (i.mailbox, i.message_id)
        parent = msg_by_pk.get(pk)
        if parent is None:
            continue
        dom = _domain((i.from_email_normalized or parent.from_addr))
        if not dom or parent.from_addr == parent.mailbox:
            continue
        bucket = by_dom[dom]
        bucket["intents"][i.intent] += 1
        bucket["sentiments"].append(float(i.sentiment or 0.0))
        if i.broker_name and not bucket["name"]:
            bucket["name"] = i.broker_name
        if i.intent == "load_offer":
            t = _as_utc(parent.sent_at)
            if t and (bucket["first_load_offer"] is None or t < bucket["first_load_offer"]):
                bucket["first_load_offer"] = t

    # Per-thread reply speed → win-rate lift.
    threads: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)  # thread -> [(sent, direction, msg_pk)]
    for m in msgs:
        d = "out" if m.from_addr == m.mailbox else "in"
        threads[m.thread_id].append((_as_utc(m.sent_at), d, f"{m.mailbox}:{m.message_id}"))

    ins_by_pk = {(i.mailbox, i.message_id): i for i in ins}

    for tid, items in threads.items():
        items.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=UTC))
        # dominant broker domain for this thread
        in_doms = [
            _domain(_lookup_from(msg_by_pk, mpk))
            for (_, d, mpk) in items if d == "in"
        ]
        in_doms = [d for d in in_doms if d]
        if not in_doms:
            continue
        bdom = Counter(in_doms).most_common(1)[0][0]
        bucket = by_dom[bdom]
        # last intent on the thread = outcome proxy
        last_ins = None
        for (_, _, mpk) in reversed(items):
            parts = mpk.split(":", 1)
            if len(parts) == 2:
                k = (parts[0], parts[1])
                if k in ins_by_pk:
                    last_ins = ins_by_pk[k]
                    break
        won = bool(last_ins and last_ins.intent in ("booked", "praise"))
        # our fastest reply latency in this thread
        best_gap_min = None
        for idx in range(1, len(items)):
            p_t, p_d, _ = items[idx - 1]
            t, d, _ = items[idx]
            if p_d == "in" and d == "out" and p_t and t:
                gap = max(0.0, (t - p_t).total_seconds() / 60)
                if best_gap_min is None or gap < best_gap_min:
                    best_gap_min = gap
        if best_gap_min is None:
            continue
        if best_gap_min < 60:
            if won:
                bucket["fast_wins"] += 1
            else:
                bucket["fast_losses"] += 1
        else:
            if won:
                bucket["slow_wins"] += 1
            else:
                bucket["slow_losses"] += 1
        # hour-of-day of our outbound replies.
        for (t, d, _) in items:
            if d == "out" and t:
                bucket["outbound_total_hours"][t.hour] += 1
        # whether a reply came after our outbound.
        for idx in range(1, len(items)):
            p_t, p_d, _ = items[idx - 1]
            t, d, _ = items[idx]
            if p_d == "out" and d == "in" and p_t:
                bucket["outbound_hours"][p_t.hour] += 1

    rows: list[BrokerPredictionRow] = []
    for dom, b in by_dom.items():
        inbound_n = len(b["inbound"])
        if inbound_n == 0:
            continue
        booked = b["intents"].get("booked", 0) + b["intents"].get("praise", 0)
        win_prob = min(1.0, booked / max(inbound_n, 1))
        avg_sent = statistics.fmean(b["sentiments"]) if b["sentiments"] else 0.0
        complaints = b["intents"].get("complaint", 0)
        praise = b["intents"].get("praise", 0)
        health = 50 + int(avg_sent * 20) + praise * 3 - complaints * 5
        last_t = max((_as_utc(m.sent_at) for m in b["inbound"] if m.sent_at), default=None)
        if last_t:
            days_silent = (now - last_t).days
            health -= max(0, days_silent - 14) // 7
        health = max(0, min(100, health))

        best_hour = None
        if b["outbound_total_hours"]:
            # pick hour with highest reply-ratio (min 2 outbounds)
            scored = [
                (h, b["outbound_hours"].get(h, 0) / tot)
                for h, tot in b["outbound_total_hours"].items() if tot >= 2
            ]
            if scored:
                best_hour = max(scored, key=lambda x: x[1])[0]

        is_slow = b["intents"].get("payment", 0) >= 2
        last30, prior30 = b["last_30d_in"], b["prior_30d_in"]
        churn = 0.0
        if prior30 > 0:
            churn = max(0.0, min(1.0, 1.0 - (last30 / prior30)))
        elif last30 == 0 and inbound_n > 2:
            churn = 1.0

        fast_rate = b["fast_wins"] / max(1, b["fast_wins"] + b["fast_losses"])
        slow_rate = b["slow_wins"] / max(1, b["slow_wins"] + b["slow_losses"])
        reply_lift = (fast_rate / slow_rate) if slow_rate > 0 else (1.0 + fast_rate)

        first_touch_latency = None
        if b["first_inbound"] and b["first_load_offer"]:
            first_touch_latency = max(
                0.0, (b["first_load_offer"] - b["first_inbound"]).total_seconds() / 86400
            )

        rows.append(
            BrokerPredictionRow(
                broker_domain=dom,
                broker_name=b["name"],
                win_probability=round(win_prob, 4),
                health_score=health,
                best_send_hour=best_hour,
                is_slow_payer=is_slow,
                churn_risk=round(churn, 4),
                reply_speed_lift=round(reply_lift, 4),
                first_touch_latency_days=(
                    round(first_touch_latency, 2) if first_touch_latency is not None else None
                ),
                computed_at=now,
            )
        )
    return rows


def _lookup_from(msg_by_pk: dict, pk: str) -> str | None:
    parts = pk.split(":", 1)
    if len(parts) != 2:
        return None
    m = msg_by_pk.get((parts[0], parts[1]))
    return m.email_lower or m.from_addr if m else None


def _compute_lane_predictions(ins: list[Any]) -> list[LanePredictionRow]:
    now = datetime.now(UTC)
    by_lane: dict[tuple[str, str, str | None], list[float]] = defaultdict(list)
    season: dict[tuple[str, str, str | None], Counter] = defaultdict(Counter)
    for i in ins:
        if not (i.lane_from and i.lane_to and i.rate_usd):
            continue
        key = (i.lane_from, i.lane_to, i.equipment)
        by_lane[key].append(float(i.rate_usd))
    rows: list[LanePredictionRow] = []
    for (o, d, eq), rates in by_lane.items():
        rows.append(
            LanePredictionRow(
                origin=o, dest=d, equipment=eq,
                price_p50=_percentile(rates, 0.5),
                price_p75=_percentile(rates, 0.75),
                price_p90=_percentile(rates, 0.9),
                season_hint={},  # month histogram would need insight.created_at over 12mo real data
                sample_size=len(rates),
                computed_at=now,
            )
        )
    return rows


def _compute_lookalikes(ins: list[Any]) -> list[LookalikeRow]:
    """Cosine on lane set — brokers that work the same lanes."""
    lanes_by_dom: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for i in ins:
        dom = _domain(i.from_email_normalized)
        if not dom or not (i.lane_from and i.lane_to):
            continue
        lanes_by_dom[dom].add((i.lane_from, i.lane_to))
    rows: list[LookalikeRow] = []
    doms = list(lanes_by_dom.keys())
    for a in doms:
        for b in doms:
            if a >= b:
                continue
            la, lb = lanes_by_dom[a], lanes_by_dom[b]
            if not la or not lb:
                continue
            inter = la & lb
            if not inter:
                continue
            score = len(inter) / ((len(la) * len(lb)) ** 0.5)
            rows.append(LookalikeRow(broker_domain=a, peer_domain=b, score=round(score, 4)))
            rows.append(LookalikeRow(broker_domain=b, peer_domain=a, score=round(score, 4)))
    return rows


def _compute_objections(ins: list[Any]) -> list[ObjectionRow]:
    """Per-broker objection clustering — keyword buckets over complaint/rejected
    intents. Deterministic rules; swapping to LLM clustering is one call site.
    """
    buckets: dict[tuple[str, str], dict] = defaultdict(lambda: {"count": 0, "exemplar": None})
    LABELS = [
        ("rate_too_high", ("too high", "price", "cheaper", "rate too")),
        ("other_carrier", ("another carrier", "other carrier", "already covered", "covered")),
        ("timing", ("too late", "no time", "already picked")),
        ("equipment", ("equipment", "trailer", "reefer")),
        ("payment_delay", ("overdue", "unpaid", "net 60", "net 90", "payment")),
        ("service", ("late delivery", "driver", "detention", "stuck")),
    ]
    for i in ins:
        if i.intent not in ("complaint", "rejected", "detention", "payment"):
            continue
        dom = _domain(i.from_email_normalized)
        if not dom:
            continue
        hay = (i.evidence or "").lower()
        for label, kws in LABELS:
            if any(k in hay for k in kws):
                b = buckets[(dom, label)]
                b["count"] += 1
                if b["exemplar"] is None:
                    b["exemplar"] = (i.evidence or "")[:200]
                break
    return [
        ObjectionRow(broker_domain=dom, label=lab, count=b["count"], exemplar=b["exemplar"])
        for (dom, lab), b in buckets.items()
    ]


async def run_nightly(session: AsyncSession) -> dict[str, int]:
    """Fan-out all nightly computes in one transaction (caller commits)."""
    msgs, ins = await _collect_corpus(session)
    brokers = _compute_broker_predictions(msgs, ins)
    lanes = _compute_lane_predictions(ins)
    looks = _compute_lookalikes(ins)
    objs = _compute_objections(ins)

    # Snapshot writes — a nightly re-run replaces last night's rows.
    await session.execute(delete(BrokerPrediction))
    await session.execute(delete(LanePrediction))
    await session.execute(delete(BrokerLookalike))
    await session.execute(delete(ObjectionCluster))

    for r in brokers:
        session.add(BrokerPrediction(
            broker_domain=r.broker_domain,
            broker_name=r.broker_name,
            win_probability=r.win_probability,
            health_score=r.health_score,
            best_send_hour=r.best_send_hour,
            is_slow_payer=r.is_slow_payer,
            churn_risk=r.churn_risk,
            reply_speed_lift=r.reply_speed_lift,
            first_touch_latency_days=r.first_touch_latency_days,
            computed_at=r.computed_at,
        ))
    for r in lanes:
        session.add(LanePrediction(
            origin=r.origin, dest=r.dest, equipment=r.equipment,
            price_p50=r.price_p50, price_p75=r.price_p75, price_p90=r.price_p90,
            season_hint=r.season_hint, sample_size=r.sample_size, computed_at=r.computed_at,
        ))
    for r in looks:
        session.add(BrokerLookalike(
            broker_domain=r.broker_domain, peer_domain=r.peer_domain, score=r.score,
        ))
    for r in objs:
        session.add(ObjectionCluster(
            broker_domain=r.broker_domain, label=r.label, count=r.count, exemplar=r.exemplar,
        ))
    session.add(PredictionRun(
        kind="analysis_nightly",
        finished_at=datetime.now(UTC),
        status="ok",
        stats={
            "brokers": len(brokers), "lanes": len(lanes),
            "lookalikes": len(looks), "objections": len(objs),
        },
    ))
    return {
        "brokers": len(brokers),
        "lanes": len(lanes),
        "lookalikes": len(looks),
        "objections": len(objs),
    }


# ---- read queries --------------------------------------------------------


async def list_broker_predictions(
    session: AsyncSession, *, broker_domain: str | None = None, limit: int = 500
) -> list[BrokerPredictionRow]:
    stmt = select(BrokerPrediction).order_by(BrokerPrediction.health_score.desc())
    if broker_domain:
        stmt = stmt.where(BrokerPrediction.broker_domain == broker_domain.lower())
    stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        BrokerPredictionRow(
            broker_domain=r.broker_domain,
            broker_name=r.broker_name,
            win_probability=float(r.win_probability or 0),
            health_score=int(r.health_score or 50),
            best_send_hour=r.best_send_hour,
            is_slow_payer=bool(r.is_slow_payer),
            churn_risk=float(r.churn_risk or 0),
            reply_speed_lift=float(r.reply_speed_lift or 1),
            first_touch_latency_days=(
                float(r.first_touch_latency_days) if r.first_touch_latency_days is not None else None
            ),
            computed_at=r.computed_at,
        )
        for r in rows
    ]


async def list_lane_predictions(
    session: AsyncSession, *, origin: str | None = None, dest: str | None = None,
    equipment: str | None = None, limit: int = 500,
) -> list[LanePredictionRow]:
    stmt = select(LanePrediction).order_by(LanePrediction.sample_size.desc())
    if origin:
        stmt = stmt.where(LanePrediction.origin.ilike(origin))
    if dest:
        stmt = stmt.where(LanePrediction.dest.ilike(dest))
    if equipment:
        stmt = stmt.where(LanePrediction.equipment == equipment)
    stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        LanePredictionRow(
            origin=r.origin, dest=r.dest, equipment=r.equipment,
            price_p50=float(r.price_p50) if r.price_p50 is not None else None,
            price_p75=float(r.price_p75) if r.price_p75 is not None else None,
            price_p90=float(r.price_p90) if r.price_p90 is not None else None,
            season_hint=r.season_hint or {},
            sample_size=int(r.sample_size or 0),
            computed_at=r.computed_at,
        )
        for r in rows
    ]


async def list_lookalikes(
    session: AsyncSession, *, broker_domain: str, top_n: int = 5
) -> list[LookalikeRow]:
    rows = (
        await session.execute(
            select(BrokerLookalike)
            .where(BrokerLookalike.broker_domain == broker_domain.lower())
            .order_by(BrokerLookalike.score.desc())
            .limit(top_n)
        )
    ).scalars().all()
    return [LookalikeRow(broker_domain=r.broker_domain, peer_domain=r.peer_domain, score=float(r.score)) for r in rows]


async def list_objections(
    session: AsyncSession, *, broker_domain: str | None = None, limit: int = 100
) -> list[ObjectionRow]:
    stmt = select(ObjectionCluster).order_by(ObjectionCluster.count.desc())
    if broker_domain:
        stmt = stmt.where(ObjectionCluster.broker_domain == broker_domain.lower())
    stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        ObjectionRow(broker_domain=r.broker_domain, label=r.label, count=int(r.count or 0), exemplar=r.exemplar)
        for r in rows
    ]


# ---- live queries (computed on request, not nightly) ---------------------


async def workload_heatmap(session: AsyncSession) -> list[WorkloadCell]:
    """Staff workload heatmap — count of our-outbound messages per (dow, hour)."""
    rows = (
        await session.execute(
            select(MailMessage.sent_at, MailMessage.from_addr, MailMessage.mailbox)
        )
    ).all()
    counter: Counter[tuple[int, int]] = Counter()
    for r in rows:
        if r.from_addr != r.mailbox:
            continue
        t = _as_utc(r.sent_at)
        if not t:
            continue
        counter[(t.weekday(), t.hour)] += 1
    return [WorkloadCell(day=d, hour=h, count=c) for (d, h), c in sorted(counter.items())]


async def thread_age_by_intent(session: AsyncSession) -> list[ThreadAgeBucket]:
    """Median + p90 'time before we answered' per intent (inbound-first).

    For each thread: find the first inbound message, then the first our-outbound
    message after it. The gap is a 'thread age before answer'. Grouped by the
    intent on that first inbound.
    """
    msgs = (await session.execute(
        select(
            MailMessage.thread_id, MailMessage.from_addr, MailMessage.mailbox,
            MailMessage.sent_at, MailMessage.mailbox, MailMessage.message_id,
        ).order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
    )).all()
    ins = (await session.execute(
        select(MessageInsight.mailbox, MessageInsight.message_id, MessageInsight.intent)
    )).all()
    intent_by_pk = {(i.mailbox, i.message_id): i.intent for i in ins}
    by_thread: dict[str, list] = defaultdict(list)
    for m in msgs:
        by_thread[m.thread_id].append(m)
    gaps_by_intent: dict[str, list[float]] = defaultdict(list)
    for tid, items in by_thread.items():
        first_in = next((x for x in items if x.from_addr != x.mailbox), None)
        if not first_in:
            continue
        reply = next(
            (x for x in items if x.from_addr == x.mailbox and _as_utc(x.sent_at) and _as_utc(x.sent_at) > _as_utc(first_in.sent_at)),
            None,
        )
        if not reply:
            continue
        gap_min = max(0.0, (_as_utc(reply.sent_at) - _as_utc(first_in.sent_at)).total_seconds() / 60)
        intent = intent_by_pk.get((first_in.mailbox, first_in.message_id)) or "routine"
        gaps_by_intent[intent].append(gap_min)
    out: list[ThreadAgeBucket] = []
    for intent, xs in sorted(gaps_by_intent.items()):
        xs = sorted(xs)
        median = int(statistics.median(xs))
        p90 = int(_percentile(xs, 0.9) or 0)
        out.append(ThreadAgeBucket(intent=intent, median_minutes=median, p90_minutes=p90, count=len(xs)))
    return out


async def loss_reasons(session: AsyncSession) -> list[LossReasonRow]:
    """Loss-reason tagging on dropped/rejected quotes.

    Uses objection cluster labels but across the whole corpus (not per-broker).
    """
    rows = (
        await session.execute(
            select(ObjectionCluster.label, func.sum(ObjectionCluster.count))
            .group_by(ObjectionCluster.label)
            .order_by(func.sum(ObjectionCluster.count).desc())
        )
    ).all()
    return [LossReasonRow(reason=r[0], count=int(r[1] or 0)) for r in rows]


async def first_touch_latency(session: AsyncSession) -> list[FirstTouchRow]:
    """Per-domain first-inbound-to-first-load-offer latency (newly-contacted brokers)."""
    rows = (
        await session.execute(
            select(
                BrokerPrediction.broker_domain, BrokerPrediction.first_touch_latency_days,
                BrokerPrediction.computed_at,
            )
            .where(BrokerPrediction.first_touch_latency_days.isnot(None))
            .order_by(BrokerPrediction.first_touch_latency_days.asc())
        )
    ).all()
    return [
        FirstTouchRow(
            broker_domain=r[0],
            first_touch_at=None,
            first_load_at=None,
            latency_days=float(r[1]) if r[1] is not None else None,
        )
        for r in rows
    ]
