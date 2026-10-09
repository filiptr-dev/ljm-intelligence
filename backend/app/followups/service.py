"""Follow-ups service — four reads composed into a board + note upsert.

Precedence (encoded by the mutually-exclusive filters in the queries, and
re-asserted here when a stray overlap appears across async timing):

    booked > replied > contacted > new

A lead with both a booked outcome and a recent reply shows up in `booked`
only. A lead with a reply shows in `replied` only. "New" means no touch
in any channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.followups import repository as repo
from app.followups.models import FollowupNote
from app.prospecting.models import Lead


class LeadNotFoundError(Exception):
    pass


@dataclass
class Board:
    new: list[dict[str, Any]]
    contacted: list[dict[str, Any]]
    replied: list[dict[str, Any]]
    booked: list[dict[str, Any]]


def _next_action_new(lead: Lead) -> dict[str, Any]:
    if lead.phone:
        return {"kind": "call", "reason": "No contact yet — call to open."}
    if lead.primary_email:
        return {"kind": "email", "reason": "No contact yet — draft an intro."}
    return {"kind": "wait", "reason": "No phone or email on file."}


def _next_action_contacted(days_waiting: int | None) -> dict[str, Any]:
    if days_waiting is None:
        return {"kind": "follow_up", "reason": "Waiting on reply."}
    if days_waiting <= 2:
        return {"kind": "wait", "reason": f"Sent {days_waiting}d ago — give it a day."}
    if days_waiting <= 7:
        return {
            "kind": "follow_up",
            "reason": f"{days_waiting}d since send — nudge or call.",
        }
    return {"kind": "call", "reason": f"{days_waiting}d silent — switch to phone."}


def _next_action_replied() -> dict[str, Any]:
    return {"kind": "call", "reason": "They replied — close with a call."}


def _next_action_booked() -> dict[str, Any]:
    return {"kind": "wait", "reason": "Booked — operations takes over."}


def _card(
    lead: Lead,
    *,
    next_action: dict[str, Any],
    days_waiting: int | None = None,
    last_activity_at: datetime | None = None,
    note: FollowupNote | None = None,
) -> dict[str, Any]:
    return {
        "lead_id": lead.id,
        "name": lead.name,
        "city": lead.city,
        "state": lead.state,
        "mc": lead.mc,
        "dot": lead.dot,
        "phone": lead.phone,
        "primary_email": lead.primary_email,
        "next_action": next_action,
        "days_waiting": days_waiting,
        "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
        "note": note.note if note else None,
        "next_touch": note.next_touch.isoformat() if note and note.next_touch else None,
    }


async def board(sessionmaker: async_sessionmaker, *, limit: int = 50) -> Board:
    async with sessionmaker() as s:
        # Serial reads keep the single AsyncSession legal — asyncio.gather
        # on one session is a race. For a four-query board the latency
        # difference is noise.
        new_rows = await repo.read_new(s, limit=limit)
        contacted_rows = await repo.read_contacted(s, limit=limit)
        replied_rows = await repo.read_replied(s, limit=limit)
        booked_rows = await repo.read_booked(s, limit=limit)

        ids = (
            [lead.id for lead in new_rows]
            + [lead.id for lead, _, _ in contacted_rows]
            + [lead.id for lead, _ in replied_rows]
            + [lead.id for lead, _ in booked_rows]
        )
        notes = await repo.read_notes(s, ids)

    # Mutual-exclusion safety net: once a lead appears in a higher-precedence
    # bucket, drop it from the lower ones even if the queries overlapped.
    seen: set[str] = set()

    booked_cards: list[dict[str, Any]] = []
    for lead, booked_at in booked_rows:
        if lead.id in seen:
            continue
        seen.add(lead.id)
        booked_cards.append(
            _card(
                lead,
                next_action=_next_action_booked(),
                last_activity_at=booked_at,
                note=notes.get(lead.id),
            )
        )

    replied_cards: list[dict[str, Any]] = []
    for lead, replied_at in replied_rows:
        if lead.id in seen:
            continue
        seen.add(lead.id)
        replied_cards.append(
            _card(
                lead,
                next_action=_next_action_replied(),
                last_activity_at=replied_at,
                note=notes.get(lead.id),
            )
        )

    contacted_cards: list[dict[str, Any]] = []
    for lead, sent_at, days_waiting in contacted_rows:
        if lead.id in seen:
            continue
        seen.add(lead.id)
        contacted_cards.append(
            _card(
                lead,
                next_action=_next_action_contacted(days_waiting),
                days_waiting=days_waiting,
                last_activity_at=sent_at,
                note=notes.get(lead.id),
            )
        )

    new_cards: list[dict[str, Any]] = []
    for lead in new_rows:
        if lead.id in seen:
            continue
        seen.add(lead.id)
        new_cards.append(
            _card(
                lead,
                next_action=_next_action_new(lead),
                last_activity_at=lead.last_seen_at,
                note=notes.get(lead.id),
            )
        )

    return Board(
        new=new_cards,
        contacted=contacted_cards,
        replied=replied_cards,
        booked=booked_cards,
    )


async def save_note(
    sessionmaker: async_sessionmaker,
    *,
    lead_id: str,
    note: str,
    next_touch: date | None,
) -> FollowupNote:
    async with sessionmaker() as s:
        if not await repo.lead_exists(s, lead_id):
            raise LeadNotFoundError(lead_id)
        return await repo.upsert_note(
            s, lead_id=lead_id, note=note, next_touch=next_touch
        )
