"""Deterministic broker-fit scorer for a Capacity Post.

Given a post + a Lead, return `(score 0-100, reason)`.

Signals (rough weights, deliberately simple + explainable):
 - Same origin state → +40 ("operates in NJ")
 - Same destination state (loads) OR post-destination in Lead's state (trucks) → +25
 - Lead current_score baseline (crawler's AI score, 0-100)                     → up to +25
 - Fresh authority (first_seen_at < 90d)                                       → +5
 - Contact fitness — has primary_email + phone                                 → +5

Kept dependency-free so it can run in tests without Gemini.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models import CapacityPost, Lead


@dataclass(frozen=True, slots=True)
class Suggestion:
    lead_id: str
    name: str
    state: str
    email: str | None
    phone: str | None
    score: int
    reason: str


def score_broker_for_post(post: CapacityPost, lead: Lead) -> Suggestion:
    parts: list[str] = []
    score = 0

    if lead.state and post.origin_state and lead.state == post.origin_state:
        score += 40
        parts.append(f"operates {post.origin_state}")

    if post.kind == "load" and post.dest_state and lead.state == post.dest_state:
        score += 25
        parts.append(f"drops in {post.dest_state}")
    elif post.kind == "truck" and post.destinations and lead.state in list(post.destinations):
        score += 25
        parts.append(f"buys into {lead.state}")

    ai_base = int(lead.current_score or 0)
    ai_contrib = round(ai_base * 0.25)
    score += ai_contrib
    if ai_base:
        parts.append(f"AI score {ai_base}")

    if lead.first_seen_at:
        fs = lead.first_seen_at
        # first_seen_at is timezone-aware from server_default; guard anyway.
        try:
            age = datetime.now(timezone.utc) - (fs if fs.tzinfo else fs.replace(tzinfo=timezone.utc))
            if age < timedelta(days=90):
                score += 5
                parts.append("new authority")
        except Exception:  # noqa: BLE001
            pass

    if lead.primary_email and lead.phone:
        score += 5

    score = max(0, min(100, score))
    reason = ", ".join(parts) if parts else "baseline"
    return Suggestion(
        lead_id=lead.id,
        name=lead.name,
        state=lead.state,
        email=lead.primary_email,
        phone=lead.phone,
        score=score,
        reason=reason,
    )
