"""ConnectorRegistry — per-tenant resolution of ports to adapters.

Reads `tenant_feature_flags` + `tenant_credentials`, instantiates the right
adapter class, memoises per `(tenant, connector)`. See the architecture plan.

Implementation stub: the current adapters still live under `app/mail/*` and
`app/sources/*`. This skeleton defines the surface so new code can be written
against it; the rehome happens in a follow-up pass (see "Deferred" in the
closeout).
"""

from __future__ import annotations

from app.integrations.ports import (
    AIProviderPort,
    EmailMailboxPort,
    EmailSenderPort,
    EnrichmentPort,
    LoadBoardPort,
)
from app.shared.tenant import TenantId


class ConnectorRegistry:
    """Resolve ports to adapters per tenant.

    v0 — not yet wired. Takes an optional `CredentialVault`; v1 reads
    `tenant_credentials` + `tenant_feature_flags` through it.
    """

    def __init__(self, vault=None) -> None:  # type: ignore[no-untyped-def]
        self._vault = vault
        self._cache: dict[tuple[str, str], object] = {}

    async def get_email_mailbox(self, tenant: TenantId) -> EmailMailboxPort:
        raise NotImplementedError("v0 — rehome `app/mail/mailbox.py` under this port")

    async def get_email_sender(self, tenant: TenantId) -> EmailSenderPort:
        raise NotImplementedError("v0 — rehome `app/mail/sender.py` under this port")

    async def get_ai_provider(self, tenant: TenantId, feature: str) -> AIProviderPort:
        raise NotImplementedError("v0 — rehome `app/sources/provider.py` under this port")

    async def get_load_boards(self, tenant: TenantId) -> list[LoadBoardPort]:
        raise NotImplementedError("v0 — rehome `app/sources/loads/*` under this port")

    async def get_enrichment(self, tenant: TenantId) -> EnrichmentPort:
        raise NotImplementedError("v0 — rehome `app/sources/fmcsa.py` under this port")
