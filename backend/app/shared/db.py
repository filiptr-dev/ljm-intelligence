"""Session + Unit-of-Work helpers.

`get_session` stays as the FastAPI dep (same shape as the legacy `app.db.get_session`;
re-exported there for backwards compat). `uow()` is the tenant-aware alternative
used by jobs and any non-HTTP entrypoint — it also runs
`SET LOCAL app.tenant_id = '<ulid>'` so the RLS policies have something to read.

Services take a session, never commit; the boundary (route/job) commits on exit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.shared.tenant import ADMIN_SENTINEL, TenantId, set_tenant


@asynccontextmanager
async def uow(
    sessionmaker: async_sessionmaker[AsyncSession],
    tenant: TenantId,
) -> AsyncIterator[AsyncSession]:
    """Open one session + one transaction + bind the tenant for RLS.

    Used by jobs and any non-HTTP entrypoint. HTTP handlers use the FastAPI dep.
    """
    set_tenant(tenant)
    async with sessionmaker() as session:
        async with session.begin():
            # SET LOCAL binds the value to the current transaction, so pgbouncer
            # transaction-mode and plain Postgres both honor it. Cast to text —
            # SET LOCAL doesn't accept bind params.
            await session.execute(text(f"SET LOCAL app.tenant_id = '{tenant}'"))
            yield session


@asynccontextmanager
async def uow_admin(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open an admin UoW — RLS sees the sentinel and permits cross-tenant reads.

    Only ops/superadmin paths use this. Writing from here is still legal; the
    policies treat the sentinel as a bypass. There is intentionally no accidental
    way to drop the tenant filter; you have to call this function by name.
    """
    set_tenant(ADMIN_SENTINEL)
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.tenant_id = ''"))
            yield session
