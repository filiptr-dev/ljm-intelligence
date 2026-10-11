"""Session factory — build an authenticated AbpClient from the vault.

The vault stores two bundles for LoadStop:

* ``connector='loadstop_login', kind='password'`` — email + password
  (operator-entered via Settings).
* ``connector='loadstop_session', kind='session'`` — the cookie dict +
  ``last_login_at`` (written by ``POST /loadboards/loadstop/test`` on
  success, re-written by auto-re-login on 401).

``open_client()`` returns a ready AbpClient wired to the stored cookies,
with a re-login callback that refreshes the session on a bounded single
retry. If no session cookie is present the caller gets
``LoadstopAuthRequired`` and should surface the Settings prompt.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

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
    async with sessionmaker() as s:
        login = await _get_bundle(s, "loadstop_login", "password")
        sess = await _get_bundle(s, "loadstop_session", "session")
    if not login or not login.get("username") or not login.get("password"):
        raise LoadstopAuthRequired("no_credentials")
    cookies = (sess or {}).get("cookies") or {}

    async def _relogin() -> None:
        result = await perform_login(
            str(login["username"]),
            str(login["password"]),
            one_time_code=login.get("one_time_code"),
        )
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
