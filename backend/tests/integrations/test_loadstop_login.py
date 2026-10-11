"""LoadStop login flow — ok / bad_credentials / needs_code (AC4).

Uses httpx MockTransport. No real network, no real credentials — the
username/password are the obviously-fake string ``bogus@invented.test``.
"""

from __future__ import annotations

import httpx

from app.integrations.adapters.loadboard.loadstop.login import perform_login

EMAIL_FORM = (
    b"<html><body>"
    b"<input name='__RequestVerificationToken' value='tok1'>"
    b"</body></html>"
)
PASSWORD_FORM_OK = (
    b"<html><body>"
    b"<input name='__RequestVerificationToken' value='tok2'>"
    b"</body></html>"
)
BAD_CREDS = b"<html><body>Invalid credentials</body></html>"
NEEDS_OTP = (
    b"<html><body>"
    b"<input name='__RequestVerificationToken' value='tok2'>"
    b"<input name='one_time_code' type='text'>"
    b"</body></html>"
)


def _handler(bad_pw: bool = False, otp_shown: bool = False):
    def h(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/Account/Login" and request.method == "GET":
            return httpx.Response(200, content=EMAIL_FORM)
        if path == "/Account/LoginEmail":
            return httpx.Response(200, content=PASSWORD_FORM_OK)
        if path == "/Account/Login" and request.method == "POST":
            if otp_shown:
                return httpx.Response(200, content=NEEDS_OTP)
            if bad_pw:
                return httpx.Response(400, content=BAD_CREDS)
            # Set the session cookie on the ok branch.
            resp = httpx.Response(302, headers={"Location": "/App/Dashboard"})
            resp.headers["set-cookie"] = ".AspNet.ApplicationCookie=abc; Path=/; HttpOnly"
            return resp
        return httpx.Response(404)

    return h


async def test_login_ok_persists_cookie():
    transport = httpx.MockTransport(_handler())
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "notreal", http=http)
    assert result.status == "ok"
    assert result.cookies and any(name.startswith(".AspNet") for name in result.cookies)


async def test_login_bad_credentials():
    transport = httpx.MockTransport(_handler(bad_pw=True))
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "wrong", http=http)
    assert result.status == "bad_credentials"


async def test_login_needs_code_when_otp_input_appears():
    transport = httpx.MockTransport(_handler(otp_shown=True))
    async with httpx.AsyncClient(transport=transport, base_url="https://secure.loadstop.com", follow_redirects=False) as http:
        result = await perform_login("bogus@invented.test", "notreal", http=http)
    assert result.status == "needs_code"
