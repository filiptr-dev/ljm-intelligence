"""DAT One scripted login.

Raw Playwright calls — the agent loop never has a ``type`` verb. The
credentials dict comes from :class:`CredentialVault` under
``connector="loadboard_login"``, ``kind="password"`` with keys
``{"email": str, "password": str}``.
"""

from __future__ import annotations

from typing import Any

from app.integrations.adapters.loadboard.agent_browser.login import (
    LoginChallenge,
    default_detect,
)

LOGIN_URL = "https://www.dat.com/login"


async def login(page: Any, credentials: dict) -> dict:
    email = str(credentials.get("email") or "")
    password = str(credentials.get("password") or "")
    if not email or not password:
        raise RuntimeError("dat_login:missing_credentials")

    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.fill("input[name='username'], input[type='email']", email)
    await page.fill("input[name='password'], input[type='password']", password)
    await page.click("button[type='submit']")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    await default_detect(page)  # raises LoginChallenge on 2FA
    return await page.context.storage_state()


__all__ = ["LoginChallenge", "login"]
