"""Tenant context — a contextvar that any layer can ask for the current tenant.

A `TenantId` is a thin NewType over `str` (ULID). We keep it a Value-Object-ish
alias to let a future `domain.py` promote it to a dataclass without rippling
through every call site.

The contextvar is set by:
  * the FastAPI `current_tenant` dependency (HTTP path), and
  * the `uow()` helper when a job calls it with an explicit tenant_id.

Reading it outside a request/job raises — a service that reaches for the
tenant with no boundary set is a bug, not a convenience.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import NewType

TenantId = NewType("TenantId", str)

# Admin sentinel — matches the RLS policy's accepted value for cross-tenant reads.
# Only `uow_admin()` ever sets this.
ADMIN_SENTINEL: TenantId = TenantId("")

_tenant_ctx: ContextVar[TenantId | None] = ContextVar("ljm_tenant_ctx", default=None)


def set_tenant(tenant: TenantId) -> None:
    """Set the current-request tenant. Called by `current_tenant` dep + `uow()`."""
    _tenant_ctx.set(tenant)


def current_tenant() -> TenantId:
    """Return the tenant bound to this execution context, or raise."""
    value = _tenant_ctx.get()
    if value is None:
        raise RuntimeError(
            "No tenant in context. A service was called outside an HTTP request or `uow()` scope."
        )
    return value


def reset_tenant() -> None:
    """Clear the tenant (test teardown; worker between jobs)."""
    _tenant_ctx.set(None)
