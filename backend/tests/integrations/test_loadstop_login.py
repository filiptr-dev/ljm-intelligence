"""LoadStop ABP cookie-session login flow.

Replicates the real 5-step dance via httpx.MockTransport. No real
network, no real credentials — the fake email is ``bogus@invented.test``.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

from app.integrations.adapters.loadboard.loadstop.login import perform_login

LOGIN_FORM_HTML = (
    b"<html><body>"
    b"<form class=\"login-form\" action=\"/Account/Login\" method=\"post\">"
    b"<input name='__RequestVerificationToken' value='rvt-2'>"
    b"<input name='usernameOrEmailAddress' type='text'>"
    b"<input name='password' type='password'>"
    b"<input name='rememberMe' type='checkbox'>"
    b"<input name='returnUrl' type='hidden'>"
    b"<input name='returnUrlHash' type='hidden'>"
    b"<input name='provider' type='hidden'>"
    b"<input name='ss' type='hidden'>"
    b"</form></body></html>"
)
LOGIN_FORM_WITH_OTP = (
    b"<html><body>"
    b"<form class=\"login-form\" action=\"/Account/Login\" method=\"post\">"
    b"<input name='__RequestVerificationToken' value='rvt-2'>"
    b"<input name='usernameOrEmailAddress' type='text'>"
    b"<input name='password' type='password'>"
    b"<input name='TwoFactorCode' type='text'>"
    b"</form></body></html>"
)


def _seed_cookies() -> dict[str, str]:
    """Headers the step-1 GET /Account/Login response plants on the client."""
    return {
        "set-cookie": (
            "XSRF-TOKEN=xsrf-1; Path=/; "
            ".AspNetCore.Antiforgery.ZYKL=af-1; Path=/; "
        ),
    }


def _handler(*, bad_password: bool = False, otp_shown: bool = False, no_tenant: bool = False):
    def h(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        # Step 1 — seed cookies.
        if path == "/Account/Login" and request.method == "GET" and "userNameOrEmailAddress" not in request.url.query.decode():
            return httpx.Response(
                200,
                content=b"<html><body>login page</body></html>",
                headers=[
                    ("set-cookie", "XSRF-TOKEN=xsrf-1; Path=/"),
                    ("set-cookie", ".AspNetCore.Antiforgery.ZYKL=af-1; Path=/"),
                ],
            )

        # Step 2 — tenant lookup.
        if path == "/api/services/app/CommonLookup/GetAllTenant":
            if request.headers.get("X-XSRF-TOKEN") != "xsrf-1":
                return httpx.Response(400, json={"error": "missing_xsrf"})
            tenants = [] if no_tenant else [{"tenancyName": "LJM", "id": 957, "isActive": True}]
            return httpx.Response(200, json={"result": tenants, "success": True})

        # Step 3 — resolve tenant.
        if path == "/api/services/app/Account/IsTenantAvailable" and request.method == "POST":
            return httpx.Response(200, json={"result": {"state": 1, "tenantId": 957}, "success": True})

        # Step 4 — login form.
        if path == "/Account/Login" and request.method == "GET":
            body = LOGIN_FORM_WITH_OTP if otp_shown else LOGIN_FORM_HTML
            return httpx.Response(200, content=body)

        # Step 5 — login submit.
        if path == "/Account/Login" and request.method == "POST":
            form = parse_qs(request.content.decode())
            if form.get("__RequestVerificationToken", [""])[0] != "rvt-2":
                return httpx.Response(400, json={"error": {"message": "bad_rvt"}, "success": False})
            if otp_shown:
                # Caller didn't send TwoFactorCode → ABP returns an error.
                return httpx.Response(200, json={"success": False, "error": {"message": "Two factor code required"}})
            if bad_password:
                return httpx.Response(
                    200,
                    json={"success": False, "error": {"message": "Login failed!", "details": "Invalid user name or password"}},
                )
            return httpx.Response(
                200,
                json={"success": True, "targetUrl": "/App/Dashboard"},
                headers=[("set-cookie", ".AspNetCore.Identity.Application=session-cookie; Path=/; HttpOnly")],
            )

        return httpx.Response(404, content=b"not found")

    return h


async def test_login_ok_sets_identity_cookie():
    transport = httpx.MockTransport(_handler())
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "notreal", http=http)
    assert result.status == "ok", result
    assert result.cookies and ".AspNetCore.Identity.Application" in result.cookies


async def test_login_bad_credentials_from_abp_envelope():
    transport = httpx.MockTransport(_handler(bad_password=True))
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "wrong", http=http)
    assert result.status == "bad_credentials", result


async def test_login_needs_code_when_otp_input_appears():
    transport = httpx.MockTransport(_handler(otp_shown=True))
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "notreal", http=http)
    assert result.status == "needs_code", result


async def test_login_no_tenant_is_bad_credentials():
    transport = httpx.MockTransport(_handler(no_tenant=True))
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "notreal", http=http)
    assert result.status == "bad_credentials", result


async def test_login_404_on_login_page_is_unknown_not_bad_credentials():
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"<html>Error 404 - /Error?statusCode=404</html>")

    transport = httpx.MockTransport(h)
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "notreal", http=http)
    assert result.status == "unknown", result
    assert "step1" in (result.detail or "")
