"""Session + Unit-of-Work helpers.

`uow()` is the tenant-aware session boundary used by jobs and any non-HTTP
entrypoint. It binds the tenant for RLS via `set_config('app.tenant_id',
:tid, is_local=true)` — a function form so the value flows as a bound
parameter, not an interpolated string.

Services take a session, never commit; the boundary (route/job) commits on exit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.shared.tenant import ADMIN_SENTINEL, TenantId, set_tenant

# `set_config(name, value, is_local)` is Postgres' functional equivalent of
# `SET LOCAL …` that accepts bind parameters — no SQL injection surface, no
# f-string. `is_local = true` scopes the setting to the current transaction,
# so pgbouncer transaction mode and plain Postgres both honor it. RLS policies
# read it via `current_setting('app.tenant_id', true)`.
_SET_TENANT_SQL = text("SELECT set_config('app.tenant_id', :tid, true)").bindparams(
    bindparam("tid", type_=None)
)


@asynccontextmanager
async def uow(
    sessionmaker: async_sessionmaker[AsyncSession],
    tenant: TenantId,
) -> AsyncIterator[AsyncSession]:
    """Open one session + one transaction + bind the tenant for RLS."""
    set_tenant(tenant)
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(_SET_TENANT_SQL, {"tid": str(tenant)})
            yield session


@asynccontextmanager
async def uow_admin(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open an admin UoW — RLS sees the sentinel and permits cross-tenant reads.

    Only ops/superadmin paths use this. There is intentionally no accidental
    way to drop the tenant filter; you have to call this function by name.
    """
    set_tenant(ADMIN_SENTINEL)
    async with sessionmaker() as session:
        async with session.begin():
            await session.execute(_SET_TENANT_SQL, {"tid": ""})
            yield session
