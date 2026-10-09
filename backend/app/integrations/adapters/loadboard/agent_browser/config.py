"""Resolve the agent-browser URL + token with the project's env > DB rule.

Standing project rule (honeypot: ``secrets in DB, not env``): the operator
sets the sidecar URL + token in the Settings UI; env still wins when
explicitly set on the process. The URL lives in a plain column on the
``settings`` row (migration 0041); the token lives in the vault under the
``agent_browser`` connector so it's never returned in plaintext.

No vault → no DB token (we fall through to env). Env URL blank + DB URL
blank → the driver stays disabled with ``reason="agent_browser_url_unset"``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.identity.models import SettingsRow
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentBrowserConfig:
    url: str
    token: str

    @property
    def ok(self) -> bool:
        return bool(self.url)


async def resolve(settings, sessionmaker) -> AgentBrowserConfig:
    """Return the effective URL + token, env first, then DB.

    ``settings`` is the Pydantic ``app.config.Settings`` — ``agent_browser_url``
    and ``agent_browser_token`` are populated from env by Pydantic. A blank
    value means "env didn't set it", so we read the DB row.
    """
    env_url = (getattr(settings, "agent_browser_url", "") or "").strip()
    env_token = (getattr(settings, "agent_browser_token", "") or "").strip()
    if env_url and env_token:
        return AgentBrowserConfig(url=env_url, token=env_token)

    db_url = ""
    db_token = ""
    if sessionmaker is not None:
        try:
            async with sessionmaker() as s:
                row = (
                    await s.execute(select(SettingsRow).where(SettingsRow.id == 1))
                ).scalar_one_or_none()
                if row is not None:
                    db_url = (getattr(row, "agent_browser_url", "") or "").strip()
                # Token via the vault.
                if not env_token:
                    try:
                        from app.identity.credentials import (
                            CredentialVault,
                            VaultConfigError,
                            VaultNotFound,
                        )

                        vault = await CredentialVault.for_session(s)
                        bundle = await vault.get(
                            s, TenantId(LJM_TENANT_ID), "agent_browser", "token"
                        )
                        db_token = str(bundle.get("token") or "")
                    except (VaultConfigError, VaultNotFound):
                        db_token = ""
                    except Exception as exc:  # noqa: BLE001
                        log.warning("agent_browser: vault read failed: %s", exc)
                        db_token = ""
        except Exception as exc:  # noqa: BLE001
            log.warning("agent_browser: DB resolve failed: %s", exc)

    return AgentBrowserConfig(
        url=env_url or db_url,
        token=env_token or db_token,
    )


__all__ = ["AgentBrowserConfig", "resolve"]
