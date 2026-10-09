"""Follow-ups HTTP seam."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.followups.schemas import (
    BoardCardOut,
    BoardOut,
    NextActionPill,
    SaveNoteIn,
    SaveNoteOut,
)
from app.followups.service import LeadNotFoundError, board, save_note

router = APIRouter(prefix="/followups", tags=["followups"])


def _to_card(d: dict) -> BoardCardOut:
    return BoardCardOut(
        lead_id=d["lead_id"],
        name=d["name"],
        city=d.get("city"),
        state=d.get("state"),
        mc=d.get("mc"),
        dot=d.get("dot"),
        phone=d.get("phone"),
        primary_email=d.get("primary_email"),
        next_action=NextActionPill(**d["next_action"]),
        days_waiting=d.get("days_waiting"),
        last_activity_at=d.get("last_activity_at"),
        note=d.get("note"),
        next_touch=d.get("next_touch"),
    )


@router.get("", response_model=BoardOut)
async def get_board(
    request: Request, limit: int = Query(50, ge=1, le=200)
) -> BoardOut:
    b = await board(request.app.state.sessionmaker, limit=limit)
    return BoardOut(
        new=[_to_card(c) for c in b.new],
        contacted=[_to_card(c) for c in b.contacted],
        replied=[_to_card(c) for c in b.replied],
        booked=[_to_card(c) for c in b.booked],
    )


@router.post("/{lead_id}/note", response_model=SaveNoteOut)
async def post_note(
    request: Request,
    body: SaveNoteIn,
    lead_id: str = Path(..., min_length=1, max_length=64),
) -> SaveNoteOut:
    try:
        row = await save_note(
            request.app.state.sessionmaker,
            lead_id=lead_id,
            note=body.note,
            next_touch=body.next_touch,
        )
    except LeadNotFoundError as exc:
        raise HTTPException(404, "unknown lead") from exc
    return SaveNoteOut(
        ok=True,
        lead_id=row.lead_id,
        updated_at=row.updated_at.isoformat(),
    )
