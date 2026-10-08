"""Rule-table coverage for ``app.pipeline.broker_next_action.compute``.

Honeypot: one-directional coverage is half-blind. Per rule we assert both
(a) the firing input returns the expected kind and reason prefix, and (b)
flipping one input disables that rule — proving the rule depends on what
the plan says it depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.prospecting.pipeline.broker_next_action import (
    PRIORITY,
    NextActionInput,
    compute,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


def _ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


# ---- rule 1: pending callback due today ------------------------------------


def test_rule1_callback_due_today_fires() -> None:
    r = compute(
        NextActionInput(
            pending_callback_today=True,
            pending_callback_days_ago=2,
            pending_callback_due=TODAY,
        ),
        NOW,
    )
    assert r.kind == "call"
    assert "Callback due today" in r.reason
    assert r.due_at == TODAY


def test_rule1_no_pending_falls_through() -> None:
    r = compute(NextActionInput(pending_callback_today=False, has_phone=True), NOW)
    # Rule 8 catches us (phone, zero contact).
    assert r.kind == "call" and r.reason.startswith("Call first")


# ---- rule 2: booked within 30 days -----------------------------------------


def test_rule2_recent_booked_waits() -> None:
    r = compute(
        NextActionInput(
            latest_call_outcome="booked",
            latest_call_logged_at=_ago(5),
            total_call_outcomes=1,
            has_phone=True,
        ),
        NOW,
    )
    assert r.kind == "wait"
    assert "booked" in r.reason.lower()


def test_rule2_old_booked_does_not_fire() -> None:
    r = compute(
        NextActionInput(
            latest_call_outcome="booked",
            latest_call_logged_at=_ago(45),
            total_call_outcomes=1,
            has_phone=True,
        ),
        NOW,
    )
    assert r.kind != "wait" or "booked" not in r.reason.lower()


# ---- rule 3: not_interested within 90 days ---------------------------------


def test_rule3_recent_not_interested_waits() -> None:
    r = compute(
        NextActionInput(
            latest_call_outcome="not_interested",
            latest_call_logged_at=_ago(30),
            total_call_outcomes=1,
            has_phone=True,
        ),
        NOW,
    )
    assert r.kind == "wait"
    assert "Not interested" in r.reason


def test_rule3_old_not_interested_does_not_fire() -> None:
    r = compute(
        NextActionInput(
            latest_call_outcome="not_interested",
            latest_call_logged_at=_ago(120),
            total_call_outcomes=1,
            has_phone=True,
        ),
        NOW,
    )
    assert r.kind != "wait"


# ---- rule 4: emailed within 7d, no reply -----------------------------------


def test_rule4_recent_email_awaits_reply() -> None:
    r = compute(
        NextActionInput(
            latest_sent_at=_ago(3),
            latest_sent_replied_at=None,
            total_sent=1,
            has_sendable_email=True,
        ),
        NOW,
    )
    assert r.kind == "wait"
    assert "awaiting reply" in r.reason


def test_rule4_with_reply_does_not_wait() -> None:
    r = compute(
        NextActionInput(
            latest_sent_at=_ago(3),
            latest_sent_replied_at=_ago(2),
            total_sent=1,
        ),
        NOW,
    )
    # Rule 5 claims it.
    assert r.kind == "follow_up"


# ---- rule 5: replied within 30d --------------------------------------------


def test_rule5_recent_reply_follows_up() -> None:
    r = compute(
        NextActionInput(
            latest_sent_at=_ago(10),
            latest_sent_replied_at=_ago(9),
            total_sent=1,
        ),
        NOW,
    )
    assert r.kind == "follow_up"
    assert "re-engage" in r.reason


def test_rule5_old_reply_does_not_fire() -> None:
    r = compute(
        NextActionInput(
            latest_sent_at=_ago(60),
            latest_sent_replied_at=_ago(55),
            total_sent=1,
        ),
        NOW,
    )
    assert r.kind != "follow_up" or "re-engage" not in r.reason


# ---- rule 6: 7-21d since email, no reply, sendable email -------------------


def test_rule6_nudge_window_fires() -> None:
    r = compute(
        NextActionInput(
            latest_sent_at=_ago(10),
            latest_sent_replied_at=None,
            total_sent=1,
            has_sendable_email=True,
        ),
        NOW,
    )
    assert r.kind == "email"
    assert "nudge" in r.reason


def test_rule6_without_sendable_email_does_not_fire() -> None:
    r = compute(
        NextActionInput(
            latest_sent_at=_ago(10),
            latest_sent_replied_at=None,
            total_sent=1,
            has_sendable_email=False,
        ),
        NOW,
    )
    assert r.kind != "email"


# ---- rule 7: no_answer within 7d, has phone --------------------------------


def test_rule7_recent_no_answer_calls_again() -> None:
    r = compute(
        NextActionInput(
            latest_call_outcome="no_answer",
            latest_call_logged_at=_ago(3),
            total_call_outcomes=1,
            has_phone=True,
        ),
        NOW,
    )
    assert r.kind == "call"
    assert "No answer" in r.reason


def test_rule7_without_phone_does_not_fire() -> None:
    r = compute(
        NextActionInput(
            latest_call_outcome="no_answer",
            latest_call_logged_at=_ago(3),
            total_call_outcomes=1,
            has_phone=False,
        ),
        NOW,
    )
    assert r.kind != "call"


# ---- rule 8: phone + zero contact ------------------------------------------


def test_rule8_fresh_phone_calls_first() -> None:
    r = compute(NextActionInput(has_phone=True), NOW)
    assert r.kind == "call"
    assert r.reason.startswith("Call first")


def test_rule8_without_phone_does_not_fire() -> None:
    r = compute(NextActionInput(has_phone=False, has_sendable_email=True), NOW)
    assert r.kind == "email"  # rule 9 takes over


# ---- rule 9: email + zero contact ------------------------------------------


def test_rule9_fresh_email_intro() -> None:
    r = compute(NextActionInput(has_phone=False, has_sendable_email=True), NOW)
    assert r.kind == "email"
    assert "intro email" in r.reason


def test_rule9_without_email_does_not_fire() -> None:
    r = compute(NextActionInput(has_phone=False, has_sendable_email=False), NOW)
    assert r.kind == "follow_up"  # rule 10


# ---- rule 10: dormant catch-all --------------------------------------------


def test_rule10_dormant_catch_all() -> None:
    # Old email, no reply, no sendable channel left.
    r = compute(
        NextActionInput(
            latest_sent_at=_ago(90),
            latest_sent_replied_at=None,
            total_sent=1,
            has_sendable_email=False,
        ),
        NOW,
    )
    assert r.kind == "follow_up"
    assert "Dormant" in r.reason


# ---- priority ordering ----------------------------------------------------


def test_priority_ordering_matches_plan() -> None:
    assert PRIORITY["call"] < PRIORITY["email"] < PRIORITY["follow_up"] < PRIORITY["wait"]
