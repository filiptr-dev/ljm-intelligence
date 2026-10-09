"""Agent-browser sidecar — Playwright/Chromium exposed as 5 HTTP verbs.

Deployable twin of ``backend/.../agent_browser/service.py`` — same tool
names, same request/response shapes. The backend's agent loop talks here
over ``AGENT_BROWSER_URL`` (set in the Settings UI); the sidecar has no
DB, no vault, no outbound calls other than the browser's own navigation.

Closed verb set (news-crawler ADR 0004): ``navigate``, ``read_page``,
``scroll``, ``wait``, ``finish``.

**Default closed.** This service is a public Render URL that drives a
real browser; running it without auth would turn it into an open SSRF
proxy. So:

- ``AGENT_BROWSER_TOKEN`` must be set in the env. Unset → every tool call
  returns ``401`` with ``token_not_configured``. ``/health`` stays open
  so Render's health check works.
- Every POST must carry a matching ``X-Agent-Token`` header; mismatch
  = ``401``.
- ``/navigate`` blocks RFC 1918 / loopback / link-local / cloud-metadata
  hosts (SSRF). ``http://localhost``, ``http://169.254.169.254``,
  ``http://10.0.0.5`` etc. are refused with ``400 private_or_internal_host``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"navigate", "read_page", "scroll", "wait", "finish"}
)

# Idle context sweeper — if a session hasn't been touched in this many seconds,
# its Playwright context is closed to reclaim memory. Keeps per-session leakage
# bounded on a long-running Render dyno.
SESSION_IDLE_SECONDS = int(os.getenv("AGENT_BROWSER_IDLE_SECONDS", "600"))
SWEEP_EVERY_SECONDS = int(os.getenv("AGENT_BROWSER_SWEEP_SECONDS", "60"))
REQUIRED_TOKEN = os.getenv("AGENT_BROWSER_TOKEN", "") or ""
# Escape hatch for local dev / the contract-test suite — allow open mode
# only when deliberately opted in. Prod defaults to closed.
ALLOW_OPEN = os.getenv("AGENT_BROWSER_ALLOW_OPEN", "") in {"1", "true", "yes"}

# Hosts we never let the browser touch, even if DNS resolves them.
_METADATA_HOSTS = frozenset(
    {"metadata.google.internal", "metadata", "instance-data.ec2.internal"}
)


class NavigateIn(BaseModel):
    url: str = Field(..., min_length=4, max_length=2048)
    session_id: str = Field(..., min_length=1, max_length=64)


class ReadPageIn(BaseModel):
    session_id: str
    max_chars: int = Field(default=24000, ge=100, le=200000)


class ScrollIn(BaseModel):
    session_id: str
    pages: int = Field(default=1, ge=1, le=20)


class WaitIn(BaseModel):
    session_id: str
    ms: int = Field(..., ge=0, le=15000)


class FinishIn(BaseModel):
    session_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolOut(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


app = FastAPI(title="loads-agent-browser", version="1.0.0")


# Per-session state: page handle + last-touched epoch. Keyed by session_id so
# cookies/localStorage never leak across sources.
_sessions: dict[str, dict[str, Any]] = {}
_playwright_handle: Any = None
_browser_handle: Any = None
_sweep_task: asyncio.Task | None = None


def _check_token(x_agent_token: str | None) -> None:
    """Default-closed: no token on the sidecar = refuse every tool call.

    ``AGENT_BROWSER_ALLOW_OPEN=1`` is the escape hatch for the offline
    contract-test suite and local dev; prod must set a real token.
    """
    if not REQUIRED_TOKEN:
        if ALLOW_OPEN:
            return
        raise HTTPException(401, "token_not_configured")
    if (x_agent_token or "") != REQUIRED_TOKEN:
        raise HTTPException(401, "bad_token")


def _is_private_or_internal(host: str) -> bool:
    """True if ``host`` resolves to a loopback / private / link-local / metadata address.

    Catches both literal-IP SSRF (``http://10.0.0.5``) and DNS-rebinding
    attempts: we resolve the hostname and reject if **any** returned
    address is private. The cost (one ``getaddrinfo``) is cheap vs. the
    cost of a public browser proxying into the Render VPC.
    """
    if not host:
        return True
    normalized = host.strip().lower().rstrip(".")
    if normalized in _METADATA_HOSTS:
        return True
    # Resolve both literal IPs and hostnames.
    try:
        infos = socket.getaddrinfo(normalized, None)
    except socket.gaierror:
        # Unresolvable → let Playwright 404 instead of us faking a 400.
        return False
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


async def _ensure_browser() -> Any:
    """Lazy-start Playwright + Chromium on first request."""
    global _playwright_handle, _browser_handle
    if _browser_handle is not None:
        return _browser_handle
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(500, f"playwright_not_installed:{exc}") from exc
    _playwright_handle = await async_playwright().start()
    _browser_handle = await _playwright_handle.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    return _browser_handle


async def _get_page(session_id: str) -> Any:
    entry = _sessions.get(session_id)
    if entry is not None:
        entry["touched"] = time.time()
        return entry["page"]
    browser = await _ensure_browser()
    context = await browser.new_context()
    page = await context.new_page()
    _sessions[session_id] = {"page": page, "touched": time.time()}
    return page


async def _close_session(session_id: str) -> None:
    entry = _sessions.pop(session_id, None)
    if entry is None:
        return
    page = entry["page"]
    try:
        await page.context.close()
    except Exception:  # pragma: no cover
        log.warning("agent_browser: context close failed", exc_info=True)


async def _sweep_loop() -> None:  # pragma: no cover - background task
    while True:
        await asyncio.sleep(SWEEP_EVERY_SECONDS)
        cutoff = time.time() - SESSION_IDLE_SECONDS
        stale = [sid for sid, e in _sessions.items() if e["touched"] < cutoff]
        for sid in stale:
            log.info("agent_browser: idle-sweep %s", sid)
            await _close_session(sid)


@app.on_event("startup")
async def _startup() -> None:  # pragma: no cover - runtime
    global _sweep_task
    _sweep_task = asyncio.create_task(_sweep_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:  # pragma: no cover - runtime
    global _sweep_task, _browser_handle, _playwright_handle
    if _sweep_task is not None:
        _sweep_task.cancel()
        try:
            await _sweep_task
        except Exception:
            pass
    for sid in list(_sessions.keys()):
        await _close_session(sid)
    if _browser_handle is not None:
        try:
            await _browser_handle.close()
        except Exception:
            pass
    if _playwright_handle is not None:
        try:
            await _playwright_handle.stop()
        except Exception:
            pass


@app.get("/health")
async def health() -> dict[str, Any]:
    """Unauthenticated — Render's health check pings this without the token.

    ``auth`` is "token" when configured and "closed" when the token is unset
    (default-closed; tool calls will 401). "open" only when the operator
    deliberately enabled ``AGENT_BROWSER_ALLOW_OPEN``.
    """
    if REQUIRED_TOKEN:
        auth = "token"
    elif ALLOW_OPEN:
        auth = "open"
    else:
        auth = "closed"
    return {
        "ok": True,
        "tools": sorted(ALLOWED_TOOLS),
        "sessions": len(_sessions),
        "auth": auth,
    }


@app.post("/navigate", response_model=ToolOut)
async def navigate(
    body: NavigateIn, x_agent_token: str | None = Header(default=None)
) -> ToolOut:
    _check_token(x_agent_token)
    try:
        parsed = urlparse(body.url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"bad_url:{exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(400, "scheme_not_http_or_https")
    if _is_private_or_internal(parsed.hostname or ""):
        # SSRF guard: refuse to proxy a browser into a VPC / metadata host.
        raise HTTPException(400, "private_or_internal_host")
    page = await _get_page(body.session_id)
    try:
        await page.goto(body.url, wait_until="domcontentloaded", timeout=20000)
    except Exception as exc:  # noqa: BLE001
        return ToolOut(ok=False, error=f"navigate_failed:{type(exc).__name__}:{exc}")
    return ToolOut(ok=True, data={"url": body.url})


@app.post("/read_page", response_model=ToolOut)
async def read_page(
    body: ReadPageIn, x_agent_token: str | None = Header(default=None)
) -> ToolOut:
    _check_token(x_agent_token)
    entry = _sessions.get(body.session_id)
    if entry is None:
        raise HTTPException(400, "no_session")
    entry["touched"] = time.time()
    page = entry["page"]
    html = await page.content()
    text = _clean_page(html)
    if len(text) > body.max_chars:
        text = text[: body.max_chars]
    return ToolOut(ok=True, data={"text": text, "len": len(text)})


@app.post("/scroll", response_model=ToolOut)
async def scroll(
    body: ScrollIn, x_agent_token: str | None = Header(default=None)
) -> ToolOut:
    _check_token(x_agent_token)
    entry = _sessions.get(body.session_id)
    if entry is None:
        raise HTTPException(400, "no_session")
    entry["touched"] = time.time()
    page = entry["page"]
    for _ in range(body.pages):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(300)
    return ToolOut(ok=True, data={"scrolled": body.pages})


@app.post("/wait", response_model=ToolOut)
async def wait(
    body: WaitIn, x_agent_token: str | None = Header(default=None)
) -> ToolOut:
    _check_token(x_agent_token)
    entry = _sessions.get(body.session_id)
    if entry is None:
        raise HTTPException(400, "no_session")
    entry["touched"] = time.time()
    page = entry["page"]
    await page.wait_for_timeout(body.ms)
    return ToolOut(ok=True, data={"waited_ms": body.ms})


@app.post("/finish", response_model=ToolOut)
async def finish(
    body: FinishIn, x_agent_token: str | None = Header(default=None)
) -> ToolOut:
    """Terminal verb — closes the session and echoes the payload back."""
    _check_token(x_agent_token)
    await _close_session(body.session_id)
    return ToolOut(ok=True, data={"payload": body.payload})


def _clean_page(html: str) -> str:
    """Trafilatura if present, else a brute-force tag strip."""
    try:
        import trafilatura  # type: ignore[import-not-found]

        extracted = trafilatura.extract(html) or ""
        if extracted:
            return extracted
    except ImportError:
        pass
    import re

    stripped = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    stripped = re.sub(r"<style[\s\S]*?</style>", " ", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


__all__ = ["ALLOWED_TOOLS", "app"]
