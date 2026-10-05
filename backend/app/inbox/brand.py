"""Brand struct read from the `settings` singleton (migration 0020).

Falls back to the `frontend/src/lib/data/types.ts:CLIENT` shape so the
preview and the sent HTML agree even on a fresh instance. One function,
one call per send — the row is tiny so no caching layer needed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import SettingsRow
from app.inbox.email_render import DEFAULT_BRAND, BrandBlock


async def get_brand(session: AsyncSession) -> BrandBlock:
    """Return the brand block, with CLIENT defaults for anything unset."""
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if row is None:
        return DEFAULT_BRAND
    return BrandBlock(
        company=row.brand_company or DEFAULT_BRAND.company,
        fleet=row.brand_fleet or DEFAULT_BRAND.fleet,
        dispatcher=row.brand_dispatcher or DEFAULT_BRAND.dispatcher,
        phone=row.brand_phone or DEFAULT_BRAND.phone,
        email=row.brand_email or DEFAULT_BRAND.email,
        logo_url=row.brand_logo_url or DEFAULT_BRAND.logo_url,
    )


async def get_accent(session: AsyncSession) -> str:
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if row is None or not row.brand_accent_hex:
        return "#BC2444"
    return row.brand_accent_hex
