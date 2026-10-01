"""ConnectorRegistry — per-tenant resolution of ports to adapters.

Reads `tenant_credentials` (via `CredentialVault`) with `tenant_feature_flags`
+ env-var fallback. Memoises per `(tenant, connector[, feature])`.

Fallback hierarchy (first match wins):
    1. Tenant credentials in `tenant_credentials` (future, decrypted via vault).
    2. Env-var-backed `Settings` defaults — this is where v1 reads from today.

The vault path is wired, but v1 doesn't populate `tenant_credentials` yet;
`VaultNotFound` / `VaultConfigError` short-circuits to env. The env names stay
IDENTICAL to what the operator guide documents (see Settings).
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound
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

    `vault` is optional at construction so the registry still boots when
    `TENANT_CRED_KEY` is not set — the env-fallback path keeps working.
    """

    def __init__(self, vault: CredentialVault | None = None) -> None:
        self._vault = vault
        self._cache: dict[tuple[str, ...], Any] = {}

    def _settings(self) -> Settings:
        return get_settings()

    async def _try_vault(
        self,
        session,
        tenant: TenantId,
        connector: str,
        kind: str,
    ) -> dict[str, object] | None:
        """Attempt to load a credential bundle; return None if unavailable."""
        if self._vault is None or session is None:
            return None
        try:
            return await self._vault.get(session, tenant, connector, kind)
        except (VaultNotFound, VaultConfigError):
            return None

    async def get_email_mailbox(
        self, tenant: TenantId, *, session=None
    ) -> EmailMailboxPort:
        key = ("email_mailbox", str(tenant))
        if key in self._cache:
            return self._cache[key]
        # Vault lookup is best-effort; env-driven settings is the v1 path.
        await self._try_vault(session, tenant, "gmail", "service_account")
        from app.integrations.adapters.email.mailbox import get_mailbox_source

        src = get_mailbox_source(self._settings())
        self._cache[key] = src
        return src  # type: ignore[return-value]

    async def get_email_sender(
        self, tenant: TenantId, *, session=None, mode_override: str | None = None
    ) -> EmailSenderPort:
        key = ("email_sender", str(tenant), mode_override or "")
        if key in self._cache:
            return self._cache[key]
        await self._try_vault(session, tenant, "gmail", "service_account")
        from app.integrations.adapters.email.sender import get_mail_sender

        sender = get_mail_sender(self._settings(), mode_override=mode_override)
        self._cache[key] = sender
        return sender  # type: ignore[return-value]

    async def get_ai_provider(
        self,
        tenant: TenantId,
        feature: str,
        *,
        session=None,
        ai_features: dict | None = None,
    ) -> AIProviderPort:
        # Not memoised per-tenant: `ai_features` may change at runtime via the
        # Settings → AI matrix. The provider objects are cheap.
        await self._try_vault(session, tenant, "ai", feature)
        from app.integrations.adapters.ai.provider import get_provider

        return get_provider(feature=feature, settings=self._settings(), ai_features=ai_features)  # type: ignore[return-value]

    async def get_load_boards(
        self, tenant: TenantId, *, session=None
    ) -> list[LoadBoardPort]:
        key = ("load_boards", str(tenant))
        if key in self._cache:
            return self._cache[key]
        for conn in ("dat", "chr", "lb123", "truckstop"):
            await self._try_vault(session, tenant, conn, "api_key")
        from app.integrations.adapters.loadboard.registry import enabled_sources

        sources = enabled_sources(self._settings())
        self._cache[key] = sources
        return sources  # type: ignore[return-value]

    async def get_enrichment(
        self, tenant: TenantId, *, session=None
    ) -> EnrichmentPort:
        key = ("enrichment", str(tenant))
        if key in self._cache:
            return self._cache[key]
        await self._try_vault(session, tenant, "fmcsa", "webkey")
        from app.integrations.adapters.enrichment import fmcsa

        # fmcsa.py exposes a module-level `fetch_fmcsa`; wrap it in a tiny
        # adapter object so the port Protocol is satisfied without touching
        # the module's call sites yet.
        class _FmcsaAdapter:
            name = "fmcsa"

            async def enrich(self, company):  # pragma: no cover — adapter seam
                return await fmcsa.fetch_fmcsa(company)

        adapter = _FmcsaAdapter()
        self._cache[key] = adapter
        return adapter  # type: ignore[return-value]
