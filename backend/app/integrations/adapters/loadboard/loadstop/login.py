"""Scripted ABP cookie-session login for LoadStop (ASP.NET Zero).

Discovery (2026-10-12): the login is JS-driven. The real flow is:

1. ``GET /Account/Login`` seeds the anti-forgery + ``XSRF-TOKEN`` cookies.
2. ``GET /api/services/app/CommonLookup/GetAllTenant?emailFilter=<email>``
   with ``X-XSRF-TOKEN`` + ``X-Requested-With: XMLHttpRequest`` → the ABP
   JSON envelope ``{result: [{tenancyName, id, isActive}, ...]}``. 0 → the
   email has no tenant mapping.
3. ``POST /api/services/app/Account/IsTenantAvailable`` with
   ``{tenancyName}`` → ``{result: {state, tenantId}}``. ``state=1`` =
   available; set the ``Abp.TenantId`` cookie.
4. ``GET /Account/Login?userNameOrEmailAddress=<email>`` renders the
   login form. Scrape ``<form … action="/Account/Login">`` + the
   ``__RequestVerificationToken`` hidden input.
5. ``POST`` the form action as
   ``application/x-www-form-urlencoded`` with ``X-XSRF-TOKEN`` +
   ``X-Requested-With: XMLHttpRequest`` and the full field set
   ``{usernameOrEmailAddress, password, __RequestVerificationToken,
   rememberMe, returnUrl, returnUrlHash, provider, ss}``. ABP responds
   with the JSON envelope ``{success, targetUrl, error}``; on success
   the ``.AspNetCore.Identity.Application`` cookie is set.

Credentials come from env (``LOADSTOP_USERNAME`` / ``LOADSTOP_PASSWORD``);
passwords never log. Password is passed into ``perform_login`` by the
session factory. The resulting cookie dict is written back to the vault
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
TENANT_LOOKUP = f"{BASE_URL}/api/services/app/CommonLookup/GetAllTenant"
TENANT_AVAILABLE = f"{BASE_URL}/api/services/app/Account/IsTenantAvailable"

# RVT inputs come in two shapes: a hidden input (`value='…'`) or an AJAX
# token (`data-token='…'`). ABP uses the hidden-input form.
_RVT_RE = re.compile(
    r"name=['\"]__RequestVerificationToken['\"][^>]*value=['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_FORM_ACTION_RE = re.compile(
    r"<form[^>]+class=['\"][^'\"]*login-form[^'\"]*['\"][^>]+action=['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_INPUT_NAME_RE = re.compile(r"<input[^>]+name=['\"]([^'\"]+)['\"]", re.IGNORECASE)
_OTP_HINT_RE = re.compile(
    r"name=['\"](one_?time_?code|otp|verification_?code|TwoFactorCode)['\"]"
    r"|two[-\s]?factor|verification\s+code|otp\s+code",
    re.IGNORECASE,
)
_BAD_CRED_RE = re.compile(
    r"invalid\s+user\s*name\s+or\s+password|invalid\s+(login|credential|password|email)|incorrect\s+(password|credential)",
    re.IGNORECASE,
)
_LOCKED_RE = re.compile(
    r"(too many|rate.?limit|captcha|temporarily (locked|blocked)|account\s+is\s+locked)",
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


def _scrape_action(html: str) -> str | None:
    m = _FORM_ACTION_RE.search(html or "")
    return m.group(1) if m else None


def _classify_failure(body: str, status_code: int, *, where: str) -> LoginResult:
    """Translate a non-OK step into a LoginResult.

    404 / 5xx on an unexpected path means a flow drift (upstream changed the
    endpoint shape), not a credential problem — report ``unknown`` with the
    path so the operator debugs the flow, not the password.
    """
    text = body or ""
    if _OTP_HINT_RE.search(text):
        return LoginResult(status="needs_code", detail=f"{where}:otp_input")
    if _LOCKED_RE.search(text) or status_code == 429:
        return LoginResult(status="blocked", detail=f"{where}:http_{status_code}")
    if _BAD_CRED_RE.search(text):
        return LoginResult(status="bad_credentials", detail=f"{where}:http_{status_code}")
    # Anything else — including 404 / redirect to /Error — is a flow drift.
    snippet = text[:120].replace("\n", " ") if text else ""
    return LoginResult(status="unknown", detail=f"{where}:http_{status_code}:{snippet}")


def _ajax_headers(xsrf: str | None) -> dict[str, str]:
    h = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
    }
    if xsrf:
        h["X-XSRF-TOKEN"] = xsrf
    return h


async def perform_login(
    email: str,
    password: str,
    one_time_code: str | None = None,
    *,
    http: httpx.AsyncClient | None = None,
) -> LoginResult:
    """Run the ABP cookie-session login, returning cookies on success.

    Never logs the password. When ``http`` is passed in, caller owns the
    lifecycle (tests inject a MockTransport); otherwise we create a
    short-lived client here.
    """
    own = http is None
    client = http or httpx.AsyncClient(follow_redirects=True, timeout=20.0)
    try:
        # Step 1 — seed cookies (anti-forgery + XSRF-TOKEN + ASLBSA).
        r1 = await client.get(LOGIN_PAGE)
        if r1.status_code >= 400:
            return _classify_failure(r1.text, r1.status_code, where="step1:/Account/Login")
        xsrf = client.cookies.get("XSRF-TOKEN")
        if not xsrf:
            return LoginResult(status="unknown", detail="step1:no_xsrf_cookie")

        headers = _ajax_headers(xsrf)

        # Step 2 — tenant lookup by email.
        r2 = await client.get(TENANT_LOOKUP, params={"emailFilter": email}, headers=headers)
        if r2.status_code >= 400:
            return _classify_failure(r2.text, r2.status_code, where="step2:GetAllTenant")
        try:
            tenants = (r2.json() or {}).get("result") or []
        except ValueError:
            return LoginResult(status="unknown", detail="step2:not_json")
        if not tenants:
            # No tenant mapped to this email — treat as a credential problem
            # (the operator will realise the email itself is wrong).
            return LoginResult(status="bad_credentials", detail="step2:no_tenant")
        if len(tenants) > 1:
            LOG.info("loadstop multiple tenants matched (%d) — picking first", len(tenants))
        tenancy_name = str(tenants[0].get("tenancyName") or "")
        if not tenancy_name:
            return LoginResult(status="unknown", detail="step2:tenant_missing_name")

        # Step 3 — resolve tenant id + set Abp.TenantId cookie.
        r3 = await client.post(
            TENANT_AVAILABLE,
            json={"tenancyName": tenancy_name},
            headers={**headers, "Content-Type": "application/json"},
        )
        if r3.status_code >= 400:
            return _classify_failure(r3.text, r3.status_code, where="step3:IsTenantAvailable")
        try:
            info = ((r3.json() or {}).get("result") or {})
        except ValueError:
            return LoginResult(status="unknown", detail="step3:not_json")
        state = info.get("state")
        tid = info.get("tenantId")
        if state != 1 or tid is None:
            return LoginResult(status="bad_credentials", detail=f"step3:tenant_state_{state}")
        client.cookies.set("Abp.TenantId", str(tid), domain="secure.loadstop.com")

        # Step 4 — fetch the login-form page for the chosen tenant.
        r4 = await client.get(LOGIN_PAGE, params={"userNameOrEmailAddress": email})
        if r4.status_code >= 400:
            return _classify_failure(r4.text, r4.status_code, where="step4:/Account/Login?userName")
        action = _scrape_action(r4.text) or "/Account/Login"
        rvt = _scrape_token(r4.text)
        if not rvt:
            return LoginResult(status="unknown", detail="step4:missing_rvt")
        names = set(_INPUT_NAME_RE.findall(r4.text))

        # Step 5 — AJAX form POST with the full field set the live form uses.
        form: dict[str, Any] = {
            "usernameOrEmailAddress": email,
            "password": password,
            "__RequestVerificationToken": rvt,
            "rememberMe": "false",
            "returnUrl": "",
            "returnUrlHash": "",
            "provider": "",
            "ss": "",
        }
        # Only send fields the live form actually declares — some installs
        # drop ``ss`` or ``provider`` and reject unknowns.
        form = {k: v for k, v in form.items() if k in names or k in {"usernameOrEmailAddress", "password", "__RequestVerificationToken"}}
        if one_time_code and "TwoFactorCode" in names:
            form["TwoFactorCode"] = one_time_code

        post_url = action if action.startswith("http") else f"{BASE_URL}{action}"
        r5 = await client.post(
            post_url,
            data=form,
            headers={
                # XSRF cookie value may have been rotated by step 4.
                **_ajax_headers(client.cookies.get("XSRF-TOKEN") or xsrf),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        # ABP returns a JSON envelope on AJAX. Parse before classifying.
        body = r5.text or ""
        parsed: dict[str, Any] | None = None
        try:
            parsed = r5.json()
        except ValueError:
            parsed = None

        if parsed and isinstance(parsed, dict):
            err = parsed.get("error") or {}
            msg = str((err or {}).get("message") or "") + " " + str((err or {}).get("details") or "")
            if parsed.get("success") is True:
                cookies = {c.name: c.value for c in client.cookies.jar}
                has_session = any(
                    name.startswith(".AspNetCore.Identity") or name.startswith(".AspNet") or name.lower() == "abp.auth"
                    for name in cookies
                )
                if has_session:
                    return LoginResult(status="ok", cookies=cookies)
                return LoginResult(status="unknown", detail="step5:success_without_session_cookie")
            # Explicit ABP error payload — classify from the error message.
            cls = _classify_failure(msg, r5.status_code, where="step5:/Account/Login")
            # ABP returns the "Invalid user name or password" text literally.
            if cls.status == "unknown" and _BAD_CRED_RE.search(msg):
                return LoginResult(status="bad_credentials", detail="step5:abp_error")
            return cls

        return _classify_failure(body, r5.status_code, where="step5:/Account/Login")
    finally:
        if own:
            await client.aclose()
