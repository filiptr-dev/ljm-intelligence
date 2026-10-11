"""Scripted two-step form login for LoadStop (ASP.NET Zero).

Discovery confirmed: ``/Account/LoginEmail`` posts the email, then the
password step at ``/Account/Login`` posts the password. Anti-forgery
token (``__RequestVerificationToken``) is a hidden input scraped from
each step's HTML. No captcha and the operator reports no 2FA code, but
we still classify ``needs_code`` in case an OTP input appears.

Credentials come from the vault via ``CredentialVault.get(connector=
'loadstop_login', kind='password')`` — we never read env or args for
them. The resulting session cookie dict is written back to the vault
under ``connector='loadstop_session', kind='session'``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .http import BASE_URL


LOG = logging.getLogger(__name__)

LOGIN_PAGE = f"{BASE_URL}/Account/Login"
LOGIN_EMAIL = f"{BASE_URL}/Account/LoginEmail"

_RVT_RE = re.compile(
    r"name=['\"]__RequestVerificationToken['\"][^>]*value=['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_OTP_HINT_RE = re.compile(
    r"name=['\"](one_?time_?code|otp|verification_?code|TwoFactorCode)['\"]",
    re.IGNORECASE,
)
_BAD_CRED_HINT_RE = re.compile(
    r"(invalid|incorrect)\s+(login|credential|password|email)",
    re.IGNORECASE,
)
_BLOCKED_HINT_RE = re.compile(
    r"(too many|rate.?limit|captcha|temporarily (locked|blocked))",
    re.IGNORECASE,
)


@dataclass
class LoginResult:
    status: str  # 'ok' | 'bad_credentials' | 'needs_code' | 'blocked' | 'unknown'
    cookies: dict[str, str] | None = None
    detail: str | None = None


def _scrape_token(html: str) -> str | None:
    m = _RVT_RE.search(html or "")
    return m.group(1) if m else None


def _classify_failure(html: str, status_code: int) -> str:
    if _OTP_HINT_RE.search(html or ""):
        return "needs_code"
    if _BLOCKED_HINT_RE.search(html or "") or status_code in (429, 403):
        return "blocked"
    if _BAD_CRED_HINT_RE.search(html or "") or status_code == 400:
        return "bad_credentials"
    return "unknown"


async def perform_login(
    email: str,
    password: str,
    one_time_code: str | None = None,
    *,
    http: httpx.AsyncClient | None = None,
) -> LoginResult:
    """Run the two-step form, returning cookies on success.

    Never logs the password; the HTTP client's logger is quiet by default.
    When ``http`` is passed in, caller owns the lifecycle (tests inject a
    MockTransport); otherwise we create a short-lived client here.
    """
    own = http is None
    client = http or httpx.AsyncClient(follow_redirects=True, timeout=15.0)
    try:
        # Step 1 — scrape the first anti-forgery token.
        r1 = await client.get(LOGIN_PAGE)
        token1 = _scrape_token(r1.text)
        if not token1:
            return LoginResult(status="unknown", detail="missing_rvt_step1")

        # Step 2 — post the email.
        r2 = await client.post(
            LOGIN_EMAIL,
            data={"LoginEmail": email, "__RequestVerificationToken": token1},
        )
        if r2.status_code >= 400:
            return LoginResult(status=_classify_failure(r2.text, r2.status_code))

        token2 = _scrape_token(r2.text) or token1

        # Step 3 — post the password (+ OTP if the vault held one).
        payload: dict[str, Any] = {
            "Password": password,
            "__RequestVerificationToken": token2,
            "LoginEmail": email,
        }
        if one_time_code:
            payload["TwoFactorCode"] = one_time_code

        r3 = await client.post(LOGIN_PAGE, data=payload)

        # LoadStop sets ``.AspNet.ApplicationCookie`` on success; a redirect
        # back to the login form means failure.
        cookies = {c.name: c.value for c in client.cookies.jar}
        has_session = any(
            name.startswith(".AspNet") or name.lower() == "abp.auth"
            for name in cookies
        )
        if r3.status_code < 400 and has_session:
            return LoginResult(status="ok", cookies=cookies)
        return LoginResult(status=_classify_failure(r3.text, r3.status_code))
    finally:
        if own:
            await client.aclose()
