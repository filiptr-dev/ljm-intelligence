"""Pydantic schemas for the follow-ups board + note editor."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class NextActionPill(BaseModel):
    kind: str
    reason: str
    due_at: str | None = None


class BoardCardOut(BaseModel):
    lead_id: str
    name: str
    city: str | None = None
    state: str | None = None
    mc: str | None = None
    dot: str | None = None
    phone: str | None = None
    primary_email: str | None = None
    next_action: NextActionPill
    days_waiting: int | None = None
    last_activity_at: str | None = None
    note: str | None = None
    next_touch: str | None = None


class BoardOut(BaseModel):
    new: list[BoardCardOut] = []
    contacted: list[BoardCardOut] = []
    replied: list[BoardCardOut] = []
    booked: list[BoardCardOut] = []


class SaveNoteIn(BaseModel):
    note: str = Field(default="", max_length=4000)
    next_touch: date | None = None


class SaveNoteOut(BaseModel):
    ok: bool = True
    lead_id: str
    updated_at: str
