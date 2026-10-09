"""Pydantic I/O schemas for the vetting router."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AuthorityOut(BaseModel):
    status: str | None = None
    add_date: str | None = None
    age_days: int | None = None
    oos_date: str | None = None
    # Where this snapshot came from — 'fmcsa_live' / 'fmcsa_cache' /
    # 'lead_record'. The UI uses this to pick the right disclaimer.
    source: str | None = None


class PriorOut(BaseModel):
    last_sent_at: str | None = None
    booked_count: int = 0
    rejected_count: int = 0


class RedFlagOut(BaseModel):
    code: str
    reason: str


class VetReportOut(BaseModel):
    key: str
    mc: str | None = None
    dot: str | None = None
    legal_name: str | None = None
    dba_name: str | None = None
    verdict: Literal["safe", "caution", "avoid"]
    red_flags: list[RedFlagOut] = []
    authority: AuthorityOut
    prior: PriorOut
    suppressed: bool = False
    evidence_url: str | None = None
    stale: bool = False
    lead_id: str | None = None
    snapshot_as_of: str | None = None
