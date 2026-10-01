"""ORM-level tenant stamping.

Every tenant-owned ORM model mixes in `TenantMixin`. A SQLAlchemy
`before_insert` event listener reads the tenant from `shared.tenant.current_tenant()`
and stamps it on the row if the code path didn't set one explicitly.

Three layers of safety for a column that must never be NULL:
  1. Service code sets `tenant_id` explicitly (preferred, legible).
  2. If it forgets, this event listener stamps from the request/job context.
  3. If there's no context at all (crawler, scheduled migration path), the
     column's `server_default = LJM_TENANT_ID` (migration 0016) catches it.

Any of the three alone would be enough most days. Together they make
"forgot to set tenant_id" a non-event.
"""

from __future__ import annotations

import logging

from sqlalchemy import String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.tenant import ADMIN_SENTINEL, _tenant_ctx

log = logging.getLogger(__name__)

# LJM fallback — matches migration 0016's deterministic id. Used only when
# an insert happens outside any tenant context *and* the DB default is
# somehow missing (defensive; shouldn't fire in production).
LJM_TENANT_ID = "01LJMORGLJM00000000000000A"


class TenantMixin:
    """Column + stamping behaviour. Add to every tenant-owned model.

    `server_default = LJM_TENANT_ID` mirrors the migration — on sqlite
    `create_all` paths (unit tests) and on raw-INSERT paths that bypass the
    ORM, the DB stamps LJM so the NOT NULL never trips a surprise.
    """

    tenant_id: Mapped[str] = mapped_column(
        String(26),
        nullable=False,
        index=True,
        server_default=LJM_TENANT_ID,
    )


@event.listens_for(TenantMixin, "before_insert", propagate=True)
def _stamp_tenant_id(mapper, connection, target):  # type: ignore[no-untyped-def]
    """Fill `tenant_id` from the contextvar if the service didn't set it."""
    current = getattr(target, "tenant_id", None)
    if current:
        return
    tenant = _tenant_ctx.get()
    if tenant and tenant != ADMIN_SENTINEL:
        target.tenant_id = tenant
        return
    # No context — fall through to the DB server_default (safety net).
    # We still log it because reaching here means a code path lost the context.
    log.warning(
        "tenant_id not set on INSERT and no tenant in context; relying on DB default",
        extra={"model": type(target).__name__},
    )
