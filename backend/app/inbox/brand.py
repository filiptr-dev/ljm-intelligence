"""Brand struct read from the `settings` singleton (migration 0020).

Empty means omitted: no fake defaults (the signature is owner-editable in
Settings). One function,
one call per send — the row is tiny so no caching layer needed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import SettingsRow
from app.inbox.email_render import DEFAULT_BRAND, BrandBlock


async def get_brand(session: AsyncSession) -> BrandBlock:
    """Return the brand block exactly as configured; unset fields stay empty."""
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if row is None:
        return DEFAULT_BRAND
    return BrandBlock(
        company=row.brand_company or "",
        dispatcher=row.brand_dispatcher or "",
        phone=row.brand_phone or "",
        email=row.brand_email or "",
        logo_url=row.brand_logo_url or "",
    )


async def get_accent(session: AsyncSession) -> str:
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if row is None or not row.brand_accent_hex:
        return "#BC2444"
    return row.brand_accent_hex
