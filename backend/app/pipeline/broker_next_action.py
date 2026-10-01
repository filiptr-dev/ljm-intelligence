"""Server-computed `next_action` for broker rows.

Pure function. Zero DB access. The caller composes the inputs (latest
``call_outcomes`` row, latest ``sent_log`` row, pending callbacks today,
whether the broker has an unsuppressed email, whether it has a phone) and
``compute()`` returns the next action + a one-line reason.

Rules evaluated top-down (first match wins) — this is the plan's rule table,
numbered to match. See ``projects/ljm-intelligence/plan/2026-10-01-brokers-real-data-next-action.md``.

Why pure + top-down: an operator reads this list dozens of times a day. A
rule set they can read in 10 seconds beats a model nobody can audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

NextActionKind = Literal["call", "email", "follow_up", "wait"]

PRIORITY: dict[str, int] = {"call": 1, "email": 2, "follow_up": 3, "wait": 4}


@dataclass(frozen=True)
class NextActionInput:
    """All inputs the rule table needs. Composed by the caller."""

    # Latest call_outcomes row (any outcome), if any.
    latest_call_outcome: str | None = None
    latest_call_logged_at: datetime | None = None
    # True if any callback_at <= today exists with outcome='callback'.
    pending_callback_today: bool = False
    pending_callback_days_ago: int | None = None  # how long ago the callback row was set
    pending_callback_due: date | None = None
    # Latest sent_log row, if any.
    latest_sent_at: datetime | None = None
    latest_sent_replied_at: datetime | None = None
    # Shape signals.
    has_phone: bool = False
    has_sendable_email: bool = False  # a lead_contacts row with email, not bounced, not suppressed
    # Totals (ever) — distinguishes "never contacted" vs. "nothing recent".
    total_call_outcomes: int = 0
    total_sent: int = 0


@dataclass(frozen=True)
class NextAction:
    kind: NextActionKind
    reason: str
    due_at: date | None = None


def _days_between(now: datetime, then: datetime | None) -> int | None:
    if then is None:
        return None
    # Both are tz-aware (UTC) in prod; tolerate naive in tests.
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return max(0, (now - then).days)


def compute(inp: NextActionInput, now: datetime) -> NextAction:
    """Return the single next action per the rule table. Top-down, first match wins."""
    # 1. Callback due today.
    if inp.pending_callback_today:
        n = inp.pending_callback_days_ago if inp.pending_callback_days_ago is not None else 0
        return NextAction(
            kind="call",
            reason=f"Callback due today (set {n} day{'s' if n != 1 else ''} ago)",
            due_at=inp.pending_callback_due,
        )

    # 2. Latest activity is booked within 30 days.
    # "Latest activity" compares the latest call vs latest email; we only need
    # rule 2 to fire if the newest signal is a call that was 'booked' and ≤30d.
    latest_call_days = _days_between(now, inp.latest_call_logged_at)
    latest_sent_days = _days_between(now, inp.latest_sent_at)

    def _call_is_newest() -> bool:
        if latest_call_days is None:
            return False
        if latest_sent_days is None:
            return True
        return latest_call_days <= latest_sent_days

    def _email_is_newest() -> bool:
        if latest_sent_days is None:
            return False
        if latest_call_days is None:
            return True
        return latest_sent_days < latest_call_days

    if inp.latest_call_outcome == "booked" and _call_is_newest() and (latest_call_days or 0) <= 30:
        return NextAction(kind="wait", reason="Just booked — don't churn")

    # 3. Latest activity is not_interested within 90 days.
    if inp.latest_call_outcome == "not_interested" and _call_is_newest() and (latest_call_days or 0) <= 90:
        n = latest_call_days or 0
        return NextAction(kind="wait", reason=f"Not interested ({n}d ago) — cool-off")

    # 4. Emailed within 7 days + no reply.
    if latest_sent_days is not None and latest_sent_days <= 7 and inp.latest_sent_replied_at is None:
        return NextAction(kind="wait", reason=f"Emailed {latest_sent_days}d ago — awaiting reply")

    # 5. Replied within 30 days.
    if latest_sent_days is not None and latest_sent_days <= 30 and inp.latest_sent_replied_at is not None:
        reply_days = _days_between(now, inp.latest_sent_replied_at) or 0
        return NextAction(kind="follow_up", reason=f"Replied {reply_days}d ago — re-engage")

    # 6. 7–21 days since email + no reply + a sendable email exists.
    if (
        latest_sent_days is not None
        and 7 < latest_sent_days <= 21
        and inp.latest_sent_replied_at is None
        and inp.has_sendable_email
    ):
        return NextAction(kind="email", reason=f"No reply after {latest_sent_days}d — nudge")

    # 7. no_answer within 7 days + has phone.
    if (
        inp.latest_call_outcome == "no_answer"
        and latest_call_days is not None
        and latest_call_days <= 7
        and inp.has_phone
    ):
        return NextAction(kind="call", reason=f"No answer {latest_call_days}d ago — try again")

    # 8. Phone + zero prior contact.
    if inp.has_phone and inp.total_call_outcomes == 0 and inp.total_sent == 0:
        return NextAction(kind="call", reason="Call first — no prior contact")

    # 9. No prior contact + has sendable email.
    if inp.total_call_outcomes == 0 and inp.total_sent == 0 and inp.has_sendable_email:
        return NextAction(kind="email", reason="No prior contact — intro email")

    # 10. Dormant catch-all.
    return NextAction(kind="follow_up", reason="Dormant — pick an action")
