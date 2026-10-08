"""Broker health score — a pure Value-Object style module.

Why pure
--------
A health score is a decision. Decisions live in one place so the list page and
the detail-page gauge can never disagree. No DB access, no I/O — give it
``HealthInputs``, get ``HealthScore`` back. Unit-testable without a session.

Formula
-------
    score = clamp(0..100,
        W_RECENCY   * recency      +
        W_WIN_RATE  * win_rate     +
        W_TONE      * tone         +
        W_VOLUME    * volume)

Each component is on the 0..100 scale before weighting; weights sum to 1.0.

    recency   = 100 * max(0, 1 - days_since_last_contact / RECENCY_HORIZON)
                (RECENCY_HORIZON=90 → a broker contacted today scores 100; at 90d
                 drops to 0; linear in between — the "cold" curve the UI used.)
    win_rate  = 100 * booked_12m / max(1, booked_12m + rejected_12m)
                (thin when decided < THIN_DECIDED → the win-rate component
                 contributes HALF-WEIGHT so a 1-of-1 broker doesn't dominate.)
    tone      = 50 + 50 * clamp(-1..1, avg_sentiment)
                (MessageInsight.sentiment is in [-1..1]; mapped to 0..100.
                 None → the component drops out and remaining weights
                 are renormalised, so no-signal brokers aren't penalised for
                 silence.)
    volume    = 100 * min(1, sent_30d / VOLUME_SATURATION)
                (VOLUME_SATURATION=10 → 10 sends in 30d saturates the signal.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# ---------- weights + windows (constants with a one-line why) ---------------

# User-approved default weights (30/30/20/20). Edit here, not at call sites.
W_RECENCY: Final[float] = 0.30   # how fresh the relationship feels
W_WIN_RATE: Final[float] = 0.30  # commercial reality — do they say yes
W_TONE: Final[float] = 0.20      # how they talk to us (inbox sentiment)
W_VOLUME: Final[float] = 0.20    # how much air-time they get this month

RECENCY_HORIZON_DAYS: Final[int] = 90   # 90-day cold curve matches the old UI
WIN_RATE_WINDOW_DAYS: Final[int] = 365  # 12-month booked / rejected window
VOLUME_WINDOW_DAYS: Final[int] = 30     # 30-day send volume
VOLUME_SATURATION: Final[int] = 10      # 10 sends/30d saturates
TONE_WINDOW_DAYS: Final[int] = 180      # tone capped to replies in last 180d
THIN_DECIDED: Final[int] = 5            # <5 decided calls → win-rate "thin"


# ---------- shapes ----------------------------------------------------------


@dataclass
class HealthInputs:
    """All inputs the formula needs. No DB handles here; the service assembles."""

    days_since_last_contact: int | None  # None → recency component drops out
    booked_12m: int
    rejected_12m: int
    avg_sentiment: float | None  # [-1..1]; None → tone component drops out
    sent_30d: int


@dataclass
class HealthScore:
    """Clamped 0..100 integer plus the per-component breakdown + a thin flag.

    ``components`` is the per-component contribution AFTER weighting, so the
    detail-page legend can show "recency 24, win-rate 15, tone 10, volume 8".
    """

    score: int
    thin: bool
    components: dict[str, float] = field(default_factory=dict)


# ---------- the only public function ----------------------------------------


def compute_health(inputs: HealthInputs) -> HealthScore:
    """Compute the 0..100 health score. Deterministic. No I/O."""
    recency = _recency(inputs.days_since_last_contact)
    decided = inputs.booked_12m + inputs.rejected_12m
    thin = decided < THIN_DECIDED
    win_rate = _win_rate(inputs.booked_12m, decided)
    tone = _tone(inputs.avg_sentiment)
    volume = _volume(inputs.sent_30d)

    # Build weighted contributions. A None component (recency / tone when
    # we have no data) drops out and we renormalise the remaining weights
    # so a brand-new broker isn't punished for a missing signal.
    pieces: list[tuple[str, float | None, float]] = [
        ("recency", recency, W_RECENCY),
        ("win_rate", win_rate, W_WIN_RATE * (0.5 if thin and win_rate is not None else 1.0)),
        ("tone", tone, W_TONE),
        ("volume", volume, W_VOLUME),
    ]
    live = [(name, val, w) for name, val, w in pieces if val is not None and w > 0]
    total_w = sum(w for _, _, w in live) or 1.0

    components: dict[str, float] = {}
    raw = 0.0
    for name, val, w in live:
        contrib = (val * w) / total_w
        components[name] = round(contrib, 2)
        raw += contrib

    score = max(0, min(100, round(raw)))
    return HealthScore(score=score, thin=thin, components=components)


# ---------- component helpers (private) -------------------------------------


def _recency(days: int | None) -> float | None:
    if days is None:
        return None
    days = max(days, 0)
    return 100.0 * max(0.0, 1.0 - (days / RECENCY_HORIZON_DAYS))


def _win_rate(booked: int, decided: int) -> float | None:
    if decided <= 0:
        return None
    return 100.0 * booked / decided


def _tone(avg: float | None) -> float | None:
    if avg is None:
        return None
    clipped = max(-1.0, min(1.0, avg))
    return 50.0 + 50.0 * clipped


def _volume(sent_30d: int) -> float | None:
    sent_30d = max(sent_30d, 0)
    return 100.0 * min(1.0, sent_30d / VOLUME_SATURATION)
