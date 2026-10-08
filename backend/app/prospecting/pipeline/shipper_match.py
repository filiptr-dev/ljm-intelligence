"""Deterministic shipper matcher for a Capacity Post.

Given a post + a list of `ShipperCandidate` rows, return the top-N
`ShipperMatchRow`s ranked by lane fit, with a human-readable reason.

Signals (deliberately simple — v1, no Gemini, no network):
 - candidate.state == post.origin_state                 → +40
   reason: "Based in {STATE} — your {truck|load} origin"
 - truck: candidate.state in post.destinations          → +30
   reason: "In {STATE} — one of your truck's destinations"
 - load:  candidate.state == post.dest_state            → +30
   reason: "In {STATE} — your load's drop state"
 - Already promoted (promoted_lead_id is not None)      → +5
   reason: "Already in your leads"
 - Has primary_email                                    → +10
 - Has phone                                            → +5
 - FMCSA authority present (fmcsa_dot or fmcsa_mc)      → +5
   reason: "Registered shipper — MC {mc}" or "— DOT {dot}"

Rows that score 0 on *lane* signals (origin AND destination both miss) are
dropped — a match must be about the lane, not just contact-info bait.

Mirrors `app/pipeline/match.py` in shape so it stays trivially testable: a
pure function over pure data (ORM-shaped but no DB session needed).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.outreach.models import CapacityPost
from app.prospecting.models import ShipperCandidate


@dataclass(frozen=True, slots=True)
class ShipperMatchRow:
    candidate_id: str
    name: str
    state: str
    city: str | None
    primary_email: str | None
    phone: str | None
    score: int
    reason: str
    promoted_lead_id: str | None


def _score_one(post: CapacityPost, c: ShipperCandidate) -> ShipperMatchRow | None:
    parts: list[str] = []
    score = 0
    lane_hit = False

    cstate = (c.state or "").upper()
    origin = (post.origin_state or "").upper()

    if cstate and origin and cstate == origin:
        score += 40
        kind_word = "truck" if post.kind == "truck" else "load"
        parts.append(f"Based in {cstate} — your {kind_word} origin")
        lane_hit = True

    if post.kind == "truck":
        dests = [d.upper() for d in (post.destinations or []) if d]
        if cstate and cstate in dests:
            score += 30
            parts.append(f"In {cstate} — one of your truck's destinations")
            lane_hit = True
    else:  # load
        dest = (post.dest_state or "").upper()
        if cstate and dest and cstate == dest:
            score += 30
            parts.append(f"In {cstate} — your load's drop state")
            lane_hit = True

    if not lane_hit:
        return None

    if c.promoted_lead_id is not None:
        score += 5
        parts.append("Already in your leads")

    if c.primary_email:
        score += 10
    if c.phone:
        score += 5

    mc = c.fmcsa_mc or c.mc
    dot = c.fmcsa_dot or c.dot
    if mc:
        score += 5
        parts.append(f"Registered shipper — MC {mc}")
    elif dot:
        score += 5
        parts.append(f"Registered shipper — DOT {dot}")

    score = max(0, min(100, score))
    reason = ", ".join(parts) if parts else "lane match"

    return ShipperMatchRow(
        candidate_id=c.id,
        name=c.name,
        state=cstate,
        city=c.city,
        primary_email=c.primary_email,
        phone=c.phone,
        score=score,
        reason=reason,
        promoted_lead_id=c.promoted_lead_id,
    )


def match_shippers_for_post(
    post: CapacityPost,
    candidates: list[ShipperCandidate],
    *,
    top: int = 20,
) -> list[ShipperMatchRow]:
    scored = [row for row in (_score_one(post, c) for c in candidates) if row is not None]
    scored.sort(key=lambda r: (-r.score, r.name, r.candidate_id))
    return scored[:top]
