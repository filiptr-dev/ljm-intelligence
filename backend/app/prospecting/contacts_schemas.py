"""Contact DTOs — wire shapes for the freight-manager-contacts plan.

Kept in a separate module from ``schemas.py`` so the existing (currently
empty) placeholder for future shared DTOs doesn't collide with the
contact-feature surface. Imported from ``contacts_router.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ContactOut(BaseModel):
    id: int
    lead_id: str
    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str | None = None
    source_url: str | None = None
    linkedin_url: str | None = None
    is_decision_maker: bool = False
    confidence: str | None = None
    is_freight_manager: bool = False
    evidence_count: int = 0
    last_verified_at: str | None = None
    discovered_at: str | None = None


class ContactListOut(BaseModel):
    items: list[ContactOut]
    next_cursor: str | None = None


class RefreshIn(BaseModel):
    sources: list[Literal["fmcsa", "gemini", "site", "inbox"]] | None = None


class RefreshOut(BaseModel):
    job_id: int | None = None
    status: Literal["enqueued", "ran_inline"]
    created: int = 0
    updated: int = 0


class ComposeContactIn(BaseModel):
    tone: Literal["professional", "friendly", "direct", "persuasive"] = "professional"
    instructions: str | None = None


class CampaignIn(BaseModel):
    segment: Literal["freight_manager"] = "freight_manager"
    tone: Literal["professional", "friendly", "direct", "persuasive"] = "professional"
    limit: int = 50
    dry_run: bool = True


class CampaignRecipient(BaseModel):
    contact_id: int
    lead_id: str
    name: str | None
    email: str


class CampaignOut(BaseModel):
    recipients: list[CampaignRecipient]
    enqueued: int = 0
    dry_run: bool = True
