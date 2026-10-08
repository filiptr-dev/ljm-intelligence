"""chr scripted login — same shape as :mod:`.dat`."""

from __future__ import annotations

from typing import Any

from app.integrations.adapters.loadboard.agent_browser.login import (
    LoginChallenge,
    default_detect,
)

LOGIN_URL = "https://my.chrobinson.com/login"


async def login(page: Any, credentials: dict) -> dict:
    email = str(credentials.get("email") or credentials.get("username") or "")
    password = str(credentials.get("password") or "")
    if not email or not password:
        raise RuntimeError("chr_login:missing_credentials")

    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.fill("input[name='username'], input[name='email'], input[type='email']", email)
    await page.fill("input[name='password'], input[type='password']", password)
    await page.click("button[type='submit']")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    await default_detect(page)
    return await page.context.storage_state()


__all__ = ["login", "LoginChallenge"]
