"""Deterministic simulated whole-inbox corpus for the SimulatedMailbox.

Why this file: the inbox-analysis demo and every analysis test need a
realistic mailbox *today*, before Workspace DWD is granted. We seed one
in-memory, keyed by a stable random seed, so a backfill against the
simulated adapter yields ~150 threads across 4 staff mailboxes, 20
broker domains, 90 days, with a realistic intent + sentiment mix and a
visible fraction of no-reply cases.

The corpus feeds the SAME schema as the Gmail adapter — the ingest
layer, the analyses, the UI, every join downstream treats a simulated
message exactly like a real one. The day DWD arrives, every value is
replaced in place; nothing downstream changes.

Determinism matters: tests assert sentiment buckets and no-reply counts
on this seed, so changing the seed is a behaviour change (bump the
constant explicitly if you intend to).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.integrations.adapters.email.mailbox import RawMessage

# ------------------------------------------------------------------ dials ---

CORPUS_SEED = 20261001  # bump when you change counts; tests pin behaviour.
STAFF_MAILBOXES: tuple[str, ...] = (
    "contact@ljminternational.com",
    "ops@ljminternational.com",
    "dispatch@ljminternational.com",
    "accounts@ljminternational.com",
)
BROKER_DOMAINS: tuple[tuple[str, str], ...] = (
    ("CH Robinson", "chrobinson.com"),
    ("TQL", "tql.com"),
    ("Coyote Logistics", "coyote.com"),
    ("XPO", "xpologistics.com"),
    ("Landstar", "landstar.com"),
    ("Echo Global", "echo.com"),
    ("Werner", "werner.com"),
    ("Schneider", "schneider.com"),
    ("JB Hunt", "jbhunt.com"),
    ("Knight-Swift", "knightswift.com"),
    ("ArcBest", "arcbest.com"),
    ("Hub Group", "hubgroup.com"),
    ("RXO", "rxo.com"),
    ("Yellow", "yellowcorp.com"),
    ("TForce", "tforcefreight.com"),
    ("Mode Global", "modetransportation.com"),
    ("Nolan Logistics", "nolanlogistics.com"),
    ("Allen Lund", "allenlund.com"),
    ("England Logistics", "englandlogistics.com"),
    ("GlobalTranz", "globaltranz.com"),
)

LANES: tuple[tuple[str, str], ...] = (
    ("Los Angeles, CA", "Dallas, TX"),
    ("Atlanta, GA", "Chicago, IL"),
    ("Memphis, TN", "Newark, NJ"),
    ("Phoenix, AZ", "Denver, CO"),
    ("Seattle, WA", "Oakland, CA"),
    ("Miami, FL", "Charlotte, NC"),
    ("Houston, TX", "Nashville, TN"),
    ("Detroit, MI", "Columbus, OH"),
    ("Portland, OR", "Salt Lake City, UT"),
    ("Boston, MA", "Richmond, VA"),
)
EQUIPMENT: tuple[str, ...] = ("Van", "Reefer", "Flatbed", "Van", "Van")

# Intent definitions with sentiment weightings. Negative sentiment skews toward
# complaint/payment/rejected; positive toward praise/booked. The split is tuned
# so the demo's "Tone of broker emails" StackBar looks like a real mailbox
# (~45% neutral, ~30% positive, ~25% negative).
INTENTS: dict[str, dict] = {
    "load_offer": {
        "weight": 30,
        "sentiment": (-0.1, 0.3),
        "subject": "Load offer: {origin} to {dest}",
        "body": (
            "Hi team, we have a {equipment} load from {origin} to {dest} picking "
            "up {date}. Target rate ${rate}. Can your team cover? Let me know if "
            "this works for you."
        ),
    },
    "rate_request": {
        "weight": 15,
        "sentiment": (-0.2, 0.2),
        "subject": "Rate request — {origin} / {dest}",
        "body": (
            "What's your best all-in on {equipment} from {origin} to {dest} this "
            "week? Need to come back to the shipper by EOD."
        ),
    },
    "urgent_truck": {
        "weight": 10,
        "sentiment": (-0.4, 0.0),
        "subject": "URGENT — truck needed {origin} today",
        "body": (
            "Need a truck in {origin} TODAY. ${rate} all-in. Please call me "
            "asap, this is live and the shipper is pushing hard."
        ),
    },
    "complaint": {
        "weight": 8,
        "sentiment": (-0.8, -0.3),
        "subject": "Delivery issue — load from last week",
        "body": (
            "Driver was 6 hours late on the {origin}-{dest} load. The receiver "
            "called me three times. This is not acceptable and we need to talk "
            "about it before I give you another load."
        ),
    },
    "payment": {
        "weight": 7,
        "sentiment": (-0.6, -0.1),
        "subject": "Invoice overdue — {origin}/{dest}",
        "body": (
            "The invoice for the {origin} to {dest} load is 45 days out and "
            "still not paid. Please get this processed, it's holding up our "
            "AR close."
        ),
    },
    "detention": {
        "weight": 5,
        "sentiment": (-0.5, 0.0),
        "subject": "Detention — {origin} pickup",
        "body": (
            "Our driver sat 4 hours at the {origin} shipper. Need detention "
            "approved for this load."
        ),
    },
    "praise": {
        "weight": 10,
        "sentiment": (0.4, 0.9),
        "subject": "Great job on {origin}/{dest}",
        "body": (
            "Driver was on time, receiver was happy, paperwork clean. Let's "
            "keep running this lane together — nice work."
        ),
    },
    "booked": {
        "weight": 10,
        "sentiment": (0.3, 0.7),
        "subject": "Confirmed — {origin} to {dest} ${rate}",
        "body": (
            "You're covered on the {origin}-{dest} load, ${rate}, pickup "
            "{date}. Rate con attached, send over the driver info."
        ),
    },
    "routine": {
        "weight": 5,
        "sentiment": (-0.1, 0.2),
        "subject": "Re: {origin}/{dest}",
        "body": "Thanks, got it. Will circle back once I hear from the shipper.",
    },
}


# -------------------------------------------------------------- generation ---


@dataclass(frozen=True)
class CorpusMsg:
    """What a seed-generated message carries beyond RawMessage — the labels
    the analysis layer would otherwise have to re-derive. Kept on
    ``raw["demo"]`` so an ingest run can route around a Gemini call during
    tests (not required; the real triage job is also valid)."""

    intent: str
    sentiment: float
    is_inbound: bool
    broker: str


def _pick(rng: random.Random, pool: tuple, weights: list[int] | None = None):
    if weights is None:
        return rng.choice(pool)
    return rng.choices(pool, weights=weights, k=1)[0]


def build_corpus(now: datetime | None = None) -> dict[str, list[RawMessage]]:
    """Deterministic corpus keyed by CORPUS_SEED.

    Returns ``{mailbox: [RawMessage, ...]}`` sorted ascending by sent_at.
    ``raw["demo"]`` carries the labels for the analysis layer / tests.
    """
    rng = random.Random(CORPUS_SEED)
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    intents = list(INTENTS.keys())
    intent_weights = [INTENTS[i]["weight"] for i in intents]

    by_mailbox: dict[str, list[RawMessage]] = {m: [] for m in STAFF_MAILBOXES}
    history = 1000

    # Target: 150 messages total, so ~37/mailbox average.
    for i in range(150):
        broker_name, broker_domain = _pick(rng, BROKER_DOMAINS)
        intent = _pick(rng, tuple(intents), intent_weights)
        meta = INTENTS[intent]
        lane = _pick(rng, LANES)
        equip = _pick(rng, EQUIPMENT)
        rate = rng.choice([1800, 1950, 2100, 2250, 2400, 2550, 2800, 3100])
        days_ago = rng.randint(0, 89)
        sent_at = now - timedelta(days=days_ago, hours=rng.randint(0, 23), minutes=rng.randint(0, 59))

        # 70% inbound. no-reply tracker needs out→silence pairs so we also
        # emit staff-outbound-only threads 10% of the time.
        is_inbound = rng.random() < 0.70
        mailbox = _pick(rng, STAFF_MAILBOXES)
        broker_user = rng.choice(["ops", "dispatch", "scott", "becky", "mark", "lisa"])
        broker_email = f"{broker_user}@{broker_domain}"

        from_addr = broker_email if is_inbound else mailbox
        to_addr = mailbox if is_inbound else broker_email
        sentiment = round(rng.uniform(*meta["sentiment"]), 2)

        subject = meta["subject"].format(origin=lane[0], dest=lane[1], rate=rate)
        body = meta["body"].format(
            origin=lane[0], dest=lane[1], equipment=equip,
            date=(sent_at + timedelta(days=rng.randint(1, 3))).strftime("%b %d"),
            rate=rate,
        )
        if not is_inbound:
            # Flip the voice — we're writing to them, not them to us.
            body = f"Hi {broker_user.title()},\n\n" + body + "\n\nThanks,\nLJM"

        thread_root = hashlib.sha1(f"{broker_email}:{subject}:{days_ago // 7}".encode()).hexdigest()[:16]
        mid = hashlib.sha1(f"m:{i}:{thread_root}".encode()).hexdigest()[:16]
        history += 1

        by_mailbox[mailbox].append(
            RawMessage(
                message_id=mid,
                thread_id=f"T-{thread_root}",
                history_id=str(history),
                mailbox=mailbox,
                from_addr=from_addr,
                to_addrs=[to_addr],
                cc_addrs=[],
                subject=subject,
                sent_at=sent_at,
                received_at=sent_at,
                in_reply_to=None,
                references=[],
                body_text=body,
                body_html=f"<p>{body}</p>",
                labels=["INBOX"] if is_inbound else ["SENT"],
                raw={
                    "id": mid,
                    "snippet": body[:120],
                    "demo": {
                        "intent": intent,
                        "sentiment": sentiment,
                        "is_inbound": is_inbound,
                        "broker": broker_name,
                        "lane_from": lane[0],
                        "lane_to": lane[1],
                        "rate": rate if intent in ("load_offer", "booked", "urgent_truck") else None,
                    },
                },
            )
        )

    for m in by_mailbox.values():
        m.sort(key=lambda r: r.sent_at)
    return by_mailbox
