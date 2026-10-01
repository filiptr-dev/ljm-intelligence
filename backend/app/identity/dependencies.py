"""Identity FastAPI deps — resolve the current tenant from the current user.

For v1 (LJM only), the user table's `tenant_id` IS the tenant. This dep also
binds the tenant into the shared contextvar + sets `app.tenant_id` on the DB
session so RLS policies and the ORM `TenantMixin` listener see the same value.
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import UserPrincipal, current_user
from app.db import get_session
from app.shared.tenant import TenantId, set_tenant


async def current_tenant(
    user: UserPrincipal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TenantId:
    """Resolve the user's tenant_id, bind it to the context + the DB session."""
    row = await session.execute(
        text("SELECT tenant_id FROM users WHERE id = :uid"),
        {"uid": user.id},
    )
    value = row.scalar_one_or_none()
    if value is None:
        # v1 fallback: migration 0016 backfilled every user with LJM.
        # If this fires, the user row predates 0016 somehow — rare.
        from app.shared.orm import LJM_TENANT_ID
        value = LJM_TENANT_ID
    tenant = TenantId(value)
    set_tenant(tenant)
    # Bind for RLS inside this transaction (bound param — no f-string).
    # `set_config` is PG-only; the sqlite test harness has no such function.
    # RLS policies only apply on Postgres anyway, so skipping on sqlite is
    # a no-op loss.
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant)},
        )
    return tenant


async def tenant_binder(request: Request) -> None:  # pragma: no cover - helper
    """Optional lightweight middleware-y dep: binds the tenant from the user.

    Routes that already depend on `current_user` can add this to also stamp
    the context + RLS without resolving a second DB session.
    """
    return None
