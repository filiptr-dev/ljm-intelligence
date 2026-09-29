"""Email helpers shared by every lead source (FMCSA, Gemini discovery, backfill)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeadContact

# Deliberately basic: one @, no spaces, a dot-separated domain with a 2+ letter TLD.
# Deliverability is checked later by the sender, this only keeps obvious junk out.
_EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def normalize_email(value: object) -> str | None:
    """Trim + lowercase; return None for blanks, placeholders and malformed addresses."""
    if not isinstance(value, str):
        return None
    email = value.strip().strip("<>").strip().lower()
    if not email or len(email) > 254:
        return None
    return email if _EMAIL_RE.match(email) else None


async def add_contact_email(
    s: AsyncSession, *, lead_id: str, email: str | None, phone: str | None, source: str
) -> bool:
    """Record the email in lead_contacts once per (lead, email). Returns True if inserted."""
    if not email:
        return False
    existing = await s.execute(
        select(LeadContact.id).where(LeadContact.lead_id == lead_id, LeadContact.email == email).limit(1)
    )
    if existing.first():
        return False
    s.add(LeadContact(lead_id=lead_id, email=email, phone=phone, source=source))
    return True
