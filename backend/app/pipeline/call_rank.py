"""Deterministic Call-List ranker for the operator's daily phone queue.

Given `leads`, prior `outcomes`, open capacity `posts`, and `today`, return the top-N
`CallRow`s ordered by score desc, then lead id (stable tiebreak → same input, same list).

Mirrored on `pipeline/match.py` in shape + spirit: pure function, dependency-free,
no Gemini, table-testable.

Signals (explicit weights, easy to tune):
  - New authority (FMCSA add_date ≤ 30d)                    → +40  ("new MC, 12d")
  - Hot AI score (leads.current_score × 0.4, capped 0..40)  → up to +40
  - Matches an open capacity post (lane / origin state)     → +20  ("Fits your NJ→PA truck")
  - Stale relationship — last contact 30–90d ago            → +15  ("Last touch 47d ago")
  - Has primary_email                                       → +5
  - Callback pin bonus (scheduled callback lands today)     → +50  ("Callback due today")

Drop rules (applied before scoring):
  - No phone → drop (it's a call list).
  - Ever `not_interested` → drop.
  - `booked` in last 14d → drop.
  - `no_answer` logged today → drop.
  - `callback` with `callback_at > today` → drop (returns on its date).
  - Any outcome logged today → drop (one call per lead per day).

Data-honesty notes (see gate decision 6 in the plan):
  - FMCSA `add_date` IS persisted per lead — nested at `Lead.raw['fmcsa']['add_date']`
    as a `YYYYMMDD` string (verified 2026-09-29 on Neon: 100% of the 284 FMCSA-sourced
    leads carry it). We parse that; if the key is absent, the "new authority" signal
    simply doesn't fire (never fabricated).
  - "Last contact" real data: for phone contacts we read `call_outcomes.logged_at`
    (persisted this slice). Email contacts live in `sent_log.sent_at` — currently 0
    rows in prod, but the union path is honest: the caller passes
    `last_email_by_lead` (may be empty). No proxy, no lie: if there's no history,
    the stale-relationship chip just doesn't appear on that row.
  - `first_seen_at` is NOT used as a proxy for authority age — the FMCSA `add_date`
    is the ground truth. If we ever ingest a source without an authority date, we'll
    label the reason chip explicitly (e.g. "First seen 12d ago") rather than pretend
    it's a new MC.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.models import CallOutcome, CapacityPost, Lead

# Slice 3 will read this default in the date picker; kept here so the ranker + the UI
# never disagree on cadence.
CALLBACK_DEFAULT_OFFSET_DAYS: int = 2


@dataclass(frozen=True, slots=True)
class CallRow:
    lead_id: str
    name: str
    state: str
    city: str | None
    phone: str
    primary_email: str | None
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    opener: str = ""
    last_outcome: dict | None = None  # {"outcome": str, "logged_at": iso-str}


# ---- helpers ---------------------------------------------------------------


def _parse_fmcsa_add_date(lead: Lead) -> date | None:
    """`raw['fmcsa']['add_date']` is a YYYYMMDD string. Return a date, or None."""
    raw = getattr(lead, "raw", None) or {}
    fmcsa = raw.get("fmcsa") if isinstance(raw, dict) else None
    if not isinstance(fmcsa, dict):
        return None
    s = (fmcsa.get("add_date") or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _as_date(dt: object) -> date | None:
    if isinstance(dt, datetime):
        return dt.date()
    if isinstance(dt, date):
        return dt
    return None


def _first_name(full: str | None) -> str:
    if not full:
        return "there"
    tok = full.strip().split()
    return tok[0].title() if tok else "there"


def _post_matches_lead(post: CapacityPost, lead: Lead) -> str | None:
    """Return a short reason chip if this open post fits the lead, else None."""
    if getattr(post, "status", "open") != "open":
        return None
    ls = (lead.state or "").upper()
    os_ = (post.origin_state or "").upper()
    if post.kind == "truck":
        # Truck posts: broker either operates in the origin state OR into a preferred dest.
        dests = [(d or "").upper() for d in (post.destinations or [])]
        if ls and ls == os_:
            head = dests[0] if dests else "?"
            return f"Fits your {os_}→{head} truck"
        if ls and ls in dests:
            return f"Fits your {os_}→{ls} truck"
        return None
    if post.kind == "load":
        ds = (post.dest_state or "").upper()
        if ls and (ls == os_ or ls == ds):
            return f"Fits your {os_}→{ds or '?'} load"
    return None


# ---- outcome index ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LeadOutcomeState:
    ever_not_interested: bool
    booked_within_14d: bool
    no_answer_today: bool
    any_outcome_today: bool
    scheduled_callback_at: date | None  # latest future/today callback
    last_call_date: date | None  # any-outcome most recent, for stale calc
    latest: CallOutcome | None


def _index_outcomes(outcomes: Iterable[CallOutcome], today: date) -> dict[str, _LeadOutcomeState]:
    by_lead: dict[str, list[CallOutcome]] = defaultdict(list)
    for o in outcomes:
        by_lead[o.lead_id].append(o)
    result: dict[str, _LeadOutcomeState] = {}
    cutoff_booked = today - timedelta(days=14)
    for lid, rows in by_lead.items():
        rows_sorted = sorted(rows, key=lambda r: r.logged_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        ever_ni = any(r.outcome == "not_interested" for r in rows_sorted)
        booked_14 = any(
            r.outcome == "booked" and (_as_date(r.logged_at) or date.min) >= cutoff_booked for r in rows_sorted
        )
        na_today = any(r.outcome == "no_answer" and _as_date(r.logged_at) == today for r in rows_sorted)
        any_today = any(_as_date(r.logged_at) == today for r in rows_sorted)
        # Latest scheduled callback that is today or in the future.
        cb_dates = [
            r.callback_at for r in rows_sorted if r.outcome == "callback" and r.callback_at and r.callback_at >= today
        ]
        cb_next = min(cb_dates) if cb_dates else None
        last_call = max((_as_date(r.logged_at) for r in rows_sorted if r.logged_at), default=None)
        result[lid] = _LeadOutcomeState(
            ever_not_interested=ever_ni,
            booked_within_14d=booked_14,
            no_answer_today=na_today,
            any_outcome_today=any_today,
            scheduled_callback_at=cb_next,
            last_call_date=last_call,
            latest=rows_sorted[0] if rows_sorted else None,
        )
    return result


# ---- main ------------------------------------------------------------------


def rank_call_list(
    leads: Iterable[Lead],
    outcomes: Iterable[CallOutcome],
    posts: Iterable[CapacityPost],
    today: date,
    *,
    top_n: int = 25,
    last_email_by_lead: dict[str, date] | None = None,
) -> list[CallRow]:
    """Score, filter, sort. Deterministic: same inputs → same outputs, always.

    `last_email_by_lead` is optional (defaults to {}); pass the max `sent_log.sent_at::date`
    per lead if you want the stale-relationship signal to see email touchpoints too.
    """
    last_email_by_lead = last_email_by_lead or {}
    state_by_lead = _index_outcomes(outcomes, today)
    open_posts = [p for p in posts if getattr(p, "status", "open") == "open"]

    rows: list[CallRow] = []
    for lead in leads:
        # Hard requirement: phone.
        phone = (lead.phone or "").strip()
        if not phone:
            continue

        st = state_by_lead.get(lead.id)

        # Drop rules.
        if st and st.ever_not_interested:
            continue
        if st and st.booked_within_14d:
            continue
        if st and st.no_answer_today:
            continue
        # A scheduled callback strictly in the future → drop (returns on its date).
        if st and st.scheduled_callback_at and st.scheduled_callback_at > today:
            continue
        # One-call-per-day guard.
        if st and st.any_outcome_today:
            continue

        # Score.
        score = 0
        reasons: list[str] = []

        # Callback pin — scheduled callback lands today.
        if st and st.scheduled_callback_at == today:
            score += 50
            reasons.append("Callback due today")

        # New authority — FMCSA add_date ≤ 30d.
        add_dt = _parse_fmcsa_add_date(lead)
        if add_dt is not None:
            age_days = (today - add_dt).days
            if 0 <= age_days <= 30:
                score += 40
                reasons.append(f"New MC · {age_days}d")

        # Hot AI score — up to +40 at current_score=100.
        ai_base = int(lead.current_score or 0)
        if ai_base > 0:
            ai_contrib = max(0, min(40, round(ai_base * 0.4)))
            score += ai_contrib
            reasons.append(f"AI score {ai_base}")

        # Capacity-post fit.
        for post in open_posts:
            chip = _post_matches_lead(post, lead)
            if chip:
                score += 20
                reasons.append(chip)
                break  # one match is enough — don't stack the same signal

        # Stale relationship — 30..90d since last touch (call or email).
        last_touch_dates: list[date] = []
        if st and st.last_call_date:
            last_touch_dates.append(st.last_call_date)
        email_dt = last_email_by_lead.get(lead.id)
        if email_dt:
            last_touch_dates.append(email_dt)
        if last_touch_dates:
            last_touch = max(last_touch_dates)
            age = (today - last_touch).days
            if 30 <= age <= 90:
                score += 15
                reasons.append(f"Last touch {age}d ago")

        # Contact fitness — email present.
        if (lead.primary_email or "").strip():
            score += 5

        score = max(0, min(100, score))

        # Opener — server-rendered from stored data, no Gemini.
        opener = _render_opener(lead, add_dt, reasons)

        last_outcome_payload: dict | None = None
        if st and st.latest is not None:
            last_outcome_payload = {
                "outcome": st.latest.outcome,
                "logged_at": (st.latest.logged_at.isoformat() if st.latest.logged_at else None),
            }

        rows.append(
            CallRow(
                lead_id=lead.id,
                name=lead.name,
                state=(lead.state or "").upper(),
                city=lead.city,
                phone=phone,
                primary_email=(lead.primary_email or None),
                score=score,
                reasons=tuple(reasons),
                opener=opener,
                last_outcome=last_outcome_payload,
            )
        )

    # Deterministic order: score desc, then lead_id asc (stable tiebreak).
    rows.sort(key=lambda r: (-r.score, r.lead_id))
    return rows[: max(0, int(top_n))]


def _render_opener(lead: Lead, add_dt: date | None, reasons: list[str]) -> str:
    """One-line, human, no-Gemini opener from stored fields only."""
    company = (lead.name or "your company").strip()
    state = (lead.state or "").upper()
    first = _first_name(None)  # We don't persist a first-name yet; keep generic.
    if add_dt is not None:
        return (
            f"Hi {first}, saw {company} in {state} pulled MC "
            f"{lead.mc or lead.dot or 'authority'} on {add_dt:%b %d} — "
            "wanted to introduce LJM before someone else does."
        )
    ai = int(lead.current_score or 0)
    if ai >= 70:
        return (
            f"Hi {first}, {company} came up as a strong fit for us in {state} — "
            "have a minute to compare notes on lanes?"
        )
    return (
        f"Hi {first}, calling from LJM International — {company} in {state} "
        "came up on our list; is there a lane we can quote for you this week?"
    )
