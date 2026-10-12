"""Session factory — build an authenticated AbpClient from env + vault session.

LoadStop credentials come from ``Settings.loadstop_username`` /
``Settings.loadstop_password`` (env ``LOADSTOP_USERNAME`` / ``LOADSTOP_PASSWORD``
— user override of the standing secrets-in-DB rule). The rotating session
cookie dict is still vaulted at ``connector='loadstop_session', kind='session'``
— that is an operational artefact, not a user-typed secret, and auto-refresh
on 401 writes it back.

``open_client()`` returns a ready AbpClient wired to the stored cookies, with
a re-login callback that refreshes the session on a bounded single retry. If
env creds are missing the caller gets ``LoadstopAuthRequired("no_credentials")``
and should surface the "set env + restart" prompt.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.identity.credentials import (
    CredentialVault,
    VaultConfigError,
    VaultNotFound,
)
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId

from .http import BASE_URL, AbpClient, LoadstopAuthRequired
from .login import perform_login

LOG = logging.getLogger(__name__)


async def _get_bundle(session: AsyncSession, connector: str, kind: str) -> dict | None:
    try:
        vault = await CredentialVault.for_session(session)
    except VaultConfigError:
        return None
    try:
        return await vault.get(session, TenantId(LJM_TENANT_ID), connector, kind)
    except VaultNotFound:
        return None
    except Exception:  # noqa: BLE001
        return None


@asynccontextmanager
async def open_client(sessionmaker):
    """Yield an authenticated AbpClient; raise LoadstopAuthRequired if we can't."""
    cfg = get_settings()
    username = (cfg.loadstop_username or "").strip()
    password = cfg.loadstop_password.get_secret_value() if cfg.loadstop_password else ""
    if not username or not password:
        raise LoadstopAuthRequired("no_credentials")

    async with sessionmaker() as s:
        sess = await _get_bundle(s, "loadstop_session", "session")
    cookies = (sess or {}).get("cookies") or {}

    async def _relogin() -> None:
        result = await perform_login(username, password)
        if result.status != "ok" or not result.cookies:
            raise LoadstopAuthRequired(f"relogin_failed:{result.status}")
        http.cookies.jar.clear()
        for name, value in result.cookies.items():
            http.cookies.set(name, value)
        async with sessionmaker() as s:
            try:
                vault = await CredentialVault.for_session(s)
            except VaultConfigError:
                return
            await vault.put(
                s,
                TenantId(LJM_TENANT_ID),
                "loadstop_session",
                "session",
                {
                    "cookies": result.cookies,
                    "last_login_at": datetime.now(UTC).isoformat(),
                },
            )
            await s.commit()

    http = httpx.AsyncClient(base_url=BASE_URL, timeout=20.0, follow_redirects=False)
    for name, value in cookies.items():
        http.cookies.set(name, value)
    try:
        client = AbpClient(http, relogin=_relogin)
        yield client
    finally:
        await http.aclose()
