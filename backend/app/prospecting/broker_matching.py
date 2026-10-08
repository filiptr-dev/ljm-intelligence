"""Shared load → broker (Lead) matcher.

One ladder for every ingest path (API adapters, headless agent, paste, inbox).
A wrong link silently moves revenue to the wrong broker, so ambiguity or no
hit returns None — NULL is honest.

Ladder: exact primary_email → non-freemail email domain == Lead.domain
(unique-indexed) → normalized name (single hit only) → exact phone.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.prospecting.models import Lead
from app.prospecting.pipeline.shipper_merge import FREE_MAIL_DOMAINS

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")
_SUFFIXES = frozenset({"llc", "inc", "corp", "co", "ltd"})


def normalize_name(s: str | None) -> str:
    if not s:
        return ""
    t = _PUNCT_RE.sub(" ", s.lower())
    words = [w for w in _WS_RE.split(t.strip()) if w and w not in _SUFFIXES]
    return " ".join(words)


def email_domain(email: str | None) -> str:
    """Lower-cased domain of ``email``, or "" for missing/free-mail."""
    if not email or "@" not in email:
        return ""
    d = email.rsplit("@", 1)[1].strip().lower().removeprefix("www.")
    return "" if d in FREE_MAIL_DOMAINS else d


async def match_broker_lead(
    session: AsyncSession,
    *,
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
) -> str | None:
    if email:
        hit = (
            await session.execute(
                select(Lead.id).where(func.lower(Lead.primary_email) == email.strip().lower()).limit(2)
            )
        ).scalars().all()
        if len(hit) == 1:
            return hit[0]
        domain = email_domain(email)
        if domain:
            hit = (await session.execute(select(Lead.id).where(Lead.domain == domain).limit(2))).scalars().all()
            if len(hit) == 1:
                return hit[0]
    norm = normalize_name(name)
    if norm:
        # Candidate prefilter on first word keeps this off a full scan; the
        # exact normalized comparison happens in Python.
        first = norm.split(" ", 1)[0]
        rows = (
            await session.execute(
                select(Lead.id, Lead.name).where(func.lower(Lead.name).like(f"{first}%"))
            )
        ).all()
        hits = [lid for lid, n in rows if normalize_name(n) == norm]
        if len(hits) == 1:
            return hits[0]
    if phone:
        hit = (await session.execute(select(Lead.id).where(Lead.phone == phone).limit(2))).scalars().all()
        if len(hit) == 1:
            return hit[0]
    return None
