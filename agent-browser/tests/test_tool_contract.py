"""Scoped contract test — the 5 tool verbs the backend agent calls.

The point: lock the request/response shape to what
``backend/app/integrations/adapters/loadboard/agent_browser/agent.py``
expects. Playwright is stubbed out so this stays fast and offline.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient


class _FakePage:
    def __init__(self) -> None:
        self.last_url = ""
        self.context = types.SimpleNamespace(close=self._close)
        self._html = "<html><body><h1>Hello</h1><p>world</p></body></html>"

    async def goto(self, url: str, **_: Any) -> None:
        self.last_url = url

    async def content(self) -> str:
        return self._html

    async def evaluate(self, _script: str) -> None:
        return None

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def _close(self) -> None:
        return None


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def route(self, *_a, **_kw) -> None:
        """Stub for Playwright's `BrowserContext.route`. Must exist — the
        sidecar fails CLOSED if the guard can't be installed."""
        return None

    async def close(self) -> None:
        return None


class _FakeBrowser:
    async def new_context(self, **_: Any) -> _FakeContext:
        return _FakeContext()

    async def close(self) -> None:
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Open-mode fixture — contract tests only (prod must set a token)."""
    for mod in [m for m in list(sys.modules) if m == "app.service"]:
        del sys.modules[mod]
    monkeypatch.delenv("AGENT_BROWSER_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_BROWSER_ALLOW_OPEN", "1")
    service = importlib.import_module("app.service")

    async def _fake_ensure_browser() -> _FakeBrowser:
        return _FakeBrowser()

    monkeypatch.setattr(service, "_ensure_browser", _fake_ensure_browser)
    # Keep SSRF guard deterministic in the loop test — example.com is public.
    monkeypatch.setattr(service, "_is_private_or_internal", lambda host: False)
    return TestClient(service.app)


def test_health_lists_allowed_tools(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert sorted(body["tools"]) == ["finish", "navigate", "read_page", "scroll", "wait"]


def test_full_tool_loop_contract(client: TestClient) -> None:
    sid = "s-test-1"

    # navigate — the first call the agent always makes
    r = client.post("/navigate", json={"url": "https://example.com/", "session_id": sid})
    assert r.status_code == 200
    nav = r.json()
    assert nav["ok"] is True and nav["data"]["url"] == "https://example.com/"

    # read_page — must come back as {"ok": True, "data": {"text": "...", "len": N}}
    r = client.post("/read_page", json={"session_id": sid, "max_chars": 100})
    assert r.status_code == 200
    rp = r.json()
    assert rp["ok"] is True
    assert "text" in rp["data"] and "len" in rp["data"]
    assert "Hello" in rp["data"]["text"]

    # scroll
    r = client.post("/scroll", json={"session_id": sid, "pages": 2})
    assert r.status_code == 200
    assert r.json()["data"]["scrolled"] == 2

    # wait
    r = client.post("/wait", json={"session_id": sid, "ms": 10})
    assert r.status_code == 200
    assert r.json()["data"]["waited_ms"] == 10

    # finish — echoes the payload the agent needs for load extraction
    r = client.post("/finish", json={"session_id": sid, "payload": {"loads": []}})
    assert r.status_code == 200
    fin = r.json()
    assert fin["ok"] is True and fin["data"]["payload"] == {"loads": []}


def test_read_without_session_is_400(client: TestClient) -> None:
    r = client.post("/read_page", json={"session_id": "nope"})
    assert r.status_code == 400


def test_token_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    for mod in [m for m in list(sys.modules) if m == "app.service"]:
        del sys.modules[mod]
    monkeypatch.setenv("AGENT_BROWSER_TOKEN", "s3cret")
    monkeypatch.delenv("AGENT_BROWSER_ALLOW_OPEN", raising=False)
    service = importlib.import_module("app.service")

    async def _fake_ensure_browser() -> _FakeBrowser:
        return _FakeBrowser()

    monkeypatch.setattr(service, "_ensure_browser", _fake_ensure_browser)
    monkeypatch.setattr(service, "_is_private_or_internal", lambda host: False)
    c = TestClient(service.app)

    # No token → 401
    r = c.post("/navigate", json={"url": "https://example.com/", "session_id": "x"})
    assert r.status_code == 401

    # Right token → 200
    r = c.post(
        "/navigate",
        json={"url": "https://example.com/", "session_id": "x"},
        headers={"X-Agent-Token": "s3cret"},
    )
    assert r.status_code == 200

    # /health stays open even with a token set
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["auth"] == "token"


def test_default_closed_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``AGENT_BROWSER_TOKEN`` set → every tool call is refused."""
    for mod in [m for m in list(sys.modules) if m == "app.service"]:
        del sys.modules[mod]
    monkeypatch.delenv("AGENT_BROWSER_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_BROWSER_ALLOW_OPEN", raising=False)
    service = importlib.import_module("app.service")
    c = TestClient(service.app)

    r = c.post("/navigate", json={"url": "https://example.com/", "session_id": "x"})
    assert r.status_code == 401
    assert r.json()["detail"] == "token_not_configured"

    # /health still responds (so Render's healthcheck works) and advertises closed.
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["auth"] == "closed"


def test_ssrf_blocks_private_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """RFC-1918 / loopback / link-local / metadata hosts → 400."""
    for mod in [m for m in list(sys.modules) if m == "app.service"]:
        del sys.modules[mod]
    monkeypatch.setenv("AGENT_BROWSER_TOKEN", "s3cret")
    monkeypatch.delenv("AGENT_BROWSER_ALLOW_OPEN", raising=False)
    service = importlib.import_module("app.service")

    async def _fake_ensure_browser() -> _FakeBrowser:
        return _FakeBrowser()

    monkeypatch.setattr(service, "_ensure_browser", _fake_ensure_browser)
    c = TestClient(service.app)
    headers = {"X-Agent-Token": "s3cret"}

    # Literal private IPs — resolver returns the IP itself.
    for bad in (
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ):
        r = c.post("/navigate", json={"url": bad, "session_id": "s"}, headers=headers)
        assert r.status_code == 400, f"expected 400 for {bad}, got {r.status_code}"
        assert r.json()["detail"] == "private_or_internal_host"

    # ftp:// is a scheme refusal (before the SSRF check).
    r = c.post(
        "/navigate",
        json={"url": "ftp://files.example.com/", "session_id": "s"},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "scheme_not_http_or_https"


def test_wrong_token_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismatched X-Agent-Token → 401 bad_token (constant-time compare)."""
    for mod in [m for m in list(sys.modules) if m == "app.service"]:
        del sys.modules[mod]
    monkeypatch.setenv("AGENT_BROWSER_TOKEN", "s3cret")
    monkeypatch.delenv("AGENT_BROWSER_ALLOW_OPEN", raising=False)
    service = importlib.import_module("app.service")

    async def _fake_ensure_browser() -> _FakeBrowser:
        return _FakeBrowser()

    monkeypatch.setattr(service, "_ensure_browser", _fake_ensure_browser)
    monkeypatch.setattr(service, "_is_private_or_internal", lambda host: False)
    c = TestClient(service.app)

    r = c.post(
        "/navigate",
        json={"url": "https://example.com/", "session_id": "x"},
        headers={"X-Agent-Token": "wrong"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "bad_token"


def test_redirect_to_private_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 302 (or JS nav) that lands on 127.0.0.1 / 169.254.169.254 fails.

    The ``_request_guard`` installed on the context would normally abort
    the request mid-flight — we can't run real Playwright here, so this
    test proves the second line of defence: the post-goto URL re-check.
    """
    for mod in [m for m in list(sys.modules) if m == "app.service"]:
        del sys.modules[mod]
    monkeypatch.setenv("AGENT_BROWSER_TOKEN", "s3cret")
    monkeypatch.delenv("AGENT_BROWSER_ALLOW_OPEN", raising=False)
    service = importlib.import_module("app.service")

    for redirected_to in ("http://127.0.0.1/admin", "http://169.254.169.254/meta"):
        class _RedirectingPage(_FakePage):
            def __init__(self, final: str) -> None:
                super().__init__()
                self.url = final

            async def goto(self, url: str, **_: Any) -> None:
                # Simulates a 302 chain → final URL is self.url, not url.
                self.last_url = url

        page_instance = _RedirectingPage(redirected_to)

        class _FixedCtx:
            async def new_page(self) -> _RedirectingPage:
                return page_instance

            async def route(self, *_a, **_kw) -> None:
                return None

            async def close(self) -> None:
                return None

        class _FixedBrowser:
            async def new_context(self, **_: Any) -> _FixedCtx:
                return _FixedCtx()

            async def close(self) -> None:
                return None

        async def _fake_ensure_browser() -> _FixedBrowser:
            return _FixedBrowser()

        monkeypatch.setattr(service, "_ensure_browser", _fake_ensure_browser)
        # First hop public (example.com → public), post-check sees private.
        call_count = {"n": 0}

        def _guard(host: str) -> bool:
            call_count["n"] += 1
            # Only treat the actual redirect target as private.
            return host in {"127.0.0.1", "169.254.169.254"}

        monkeypatch.setattr(service, "_is_private_or_internal", _guard)

        service._sessions.clear()
        c = TestClient(service.app)
        r = c.post(
            "/navigate",
            json={"url": "https://example.com/", "session_id": "r"},
            headers={"X-Agent-Token": "s3cret"},
        )
        assert r.status_code == 400, (
            f"expected 400 redirected_to_private_host, got {r.status_code} / {r.text}"
        )
        assert r.json()["detail"] == "redirected_to_private_host"
