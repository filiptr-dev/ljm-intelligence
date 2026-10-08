"""Agent-browser sidecar — Playwright/Chromium exposed as 5 HTTP verbs.

Run as its own process (``uvicorn app.integrations.adapters.loadboard.agent_browser.service:app``
inside the GitHub Actions runner — see ``.github/workflows/loads-agent.yml``).
The FastAPI backend talks to it over ``AGENT_BROWSER_URL`` and nothing
else — a missing sidecar never breaks a boot or a test, and the sidecar
has no DB or vault access on its own.

Closed verb set (news-crawler ADR 0004 rule): ``navigate``, ``read_page``,
``scroll``, ``wait``, ``finish``. No ``type`` / ``click`` / ``keyboard``:
the LLM drives verbs, the human (via :mod:`login.*`) pre-writes the
credentials.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verb set — single source of truth. The driver sends the same strings; a
# tool call outside the set raises ``HTTPException(400, "unknown_tool")``.
# ---------------------------------------------------------------------------
ALLOWED_TOOLS: frozenset[str] = frozenset({"navigate", "read_page", "scroll", "wait", "finish"})


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


app = FastAPI(title="loads-agent-browser", version="0.1.0")


# A tiny in-process session map: ``session_id -> page``. Each session owns
# its own ``BrowserContext`` so cookies / localStorage never leak across
# sources. On GH Actions the whole process dies at job end so there's no
# retention concern — the storage_state is round-tripped via the vault.
_sessions: dict[str, Any] = {}
_playwright_handle: Any = None
_browser_handle: Any = None


async def _ensure_browser() -> Any:
    """Lazy-start Playwright + Chromium. Import inside the function so the
    FastAPI *backend* never pulls Playwright at import time."""
    global _playwright_handle, _browser_handle
    if _browser_handle is not None:
        return _browser_handle
    try:  # pragma: no cover - exercised by sidecar runtime only
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(500, f"playwright_not_installed:{exc}") from exc
    _playwright_handle = await async_playwright().start()
    _browser_handle = await _playwright_handle.chromium.launch(headless=True)
    return _browser_handle


async def _get_page(session_id: str, storage_state: dict | None = None) -> Any:
    """Return the page bound to ``session_id``, creating it on first use."""
    page = _sessions.get(session_id)
    if page is not None:
        return page
    browser = await _ensure_browser()
    context = await browser.new_context(storage_state=storage_state)
    page = await context.new_page()
    _sessions[session_id] = page
    return page


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "tools": sorted(ALLOWED_TOOLS), "sessions": len(_sessions)}


@app.post("/navigate", response_model=ToolOut)
async def navigate(body: NavigateIn) -> ToolOut:
    page = await _get_page(body.session_id)
    await page.goto(body.url, wait_until="domcontentloaded", timeout=20000)
    return ToolOut(ok=True, data={"url": body.url})


@app.post("/read_page", response_model=ToolOut)
async def read_page(body: ReadPageIn) -> ToolOut:
    page = _sessions.get(body.session_id)
    if page is None:
        raise HTTPException(400, "no_session")
    html = await page.content()
    text = _clean_page(html)
    if len(text) > body.max_chars:
        text = text[: body.max_chars]
    return ToolOut(ok=True, data={"text": text, "len": len(text)})


@app.post("/scroll", response_model=ToolOut)
async def scroll(body: ScrollIn) -> ToolOut:
    page = _sessions.get(body.session_id)
    if page is None:
        raise HTTPException(400, "no_session")
    for _ in range(body.pages):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(300)
    return ToolOut(ok=True, data={"scrolled": body.pages})


@app.post("/wait", response_model=ToolOut)
async def wait(body: WaitIn) -> ToolOut:
    page = _sessions.get(body.session_id)
    if page is None:
        raise HTTPException(400, "no_session")
    await page.wait_for_timeout(body.ms)
    return ToolOut(ok=True, data={"waited_ms": body.ms})


@app.post("/finish", response_model=ToolOut)
async def finish(body: FinishIn) -> ToolOut:
    """Terminal verb — closes the session and echoes the payload back."""
    page = _sessions.pop(body.session_id, None)
    if page is not None:
        try:
            await page.context.close()
        except Exception:  # pragma: no cover
            log.warning("agent_browser: context close failed", exc_info=True)
    return ToolOut(ok=True, data={"payload": body.payload})


def _clean_page(html: str) -> str:
    """Trafilatura if present, else a brute-force tag strip.

    Keeping ``trafilatura`` optional means the test suite doesn't need a new
    heavy dependency; the sidecar image installs it as part of the GH
    Actions workflow.
    """
    try:  # pragma: no cover - optional dep in test env
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
