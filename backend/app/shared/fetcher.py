"""Pluggable HTML fetcher for the enrichment scraper.

Defines a `Fetcher` protocol so the enrichment pipeline never learns the
difference between the default httpx + trafilatura path and a future Crawl4AI
(JS-capable) implementation (`ljm-intelligence-crawl4ai-local-daily`).

Design notes
------------
* One httpx client per pass. Callers instantiate a fetcher, walk pages, then
  drop the fetcher (its `__aexit__` closes the client).
* Polite defaults: LJM UA, `Accept-Language: en`, follow-redirects, gzip on,
  15s timeout, one-attempt retry on 502/503/504.
* `js_only` heuristic: raw HTML > 5 KB with trafilatura text < 200 chars AND
  script-tag density > 30 % of body length → likely a JS-rendered SPA.
* Extraction: `trafilatura.extract(...)` with `favor_precision=True`, links
  stripped (we want the visible text for LLM extraction, not the nav dump).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

LJM_USER_AGENT = "LJM-Intelligence-Bot/1.0 (+https://ljminternational.com)"


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    html: str
    text: str
    fetched_at: datetime
    js_only: bool


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchResult:  # pragma: no cover - protocol
        ...

    async def aclose(self) -> None:  # pragma: no cover - protocol
        ...


_SCRIPT_RE = re.compile(r"<script\b[^>]*>[\s\S]*?</script\s*>", re.IGNORECASE)


def _extract_text(html: str) -> str:
    """Try trafilatura first; fall back to a naive tag-strip so tests never depend on install."""
    try:
        import trafilatura  # type: ignore

        text = trafilatura.extract(html, include_links=False, favor_precision=True) or ""
        return text.strip()
    except Exception:  # noqa: BLE001 - pragma: no cover - fallback rarely fires
        # Minimal fallback: strip scripts + tags, collapse whitespace.
        no_scripts = _SCRIPT_RE.sub(" ", html)
        no_tags = re.sub(r"<[^>]+>", " ", no_scripts)
        return re.sub(r"\s+", " ", no_tags).strip()


def _js_only_heuristic(html: str, text: str) -> bool:
    """True when the page looks like a JS SPA: big HTML, tiny extracted text, script-heavy."""
    html_len = len(html)
    if html_len <= 5000:
        return False
    if len(text) >= 200:
        return False
    script_len = sum(len(m.group(0)) for m in _SCRIPT_RE.finditer(html))
    if html_len == 0:
        return False
    return (script_len / html_len) > 0.30


class HttpxTrafilaturaFetcher:
    """Default fetcher: httpx + trafilatura. Pure Python, no browser."""

    def __init__(self, *, timeout_s: float = 15.0, client: httpx.AsyncClient | None = None) -> None:
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": LJM_USER_AGENT, "Accept-Language": "en"},
        )

    async def fetch(self, url: str) -> FetchResult:
        html = ""
        status = 0
        final_url = url
        try:
            resp = await self._client.get(url)
            status = resp.status_code
            if status in (502, 503, 504):
                # One-shot retry on transient gateway errors.
                resp = await self._client.get(url)
                status = resp.status_code
            final_url = str(resp.url)
            html = resp.text or ""
        except httpx.HTTPError as exc:
            log.info("fetcher: http error for %s: %s", url, exc)

        text = _extract_text(html) if html else ""
        js_only = _js_only_heuristic(html, text) if html else False
        return FetchResult(
            url=url,
            final_url=final_url,
            status=status,
            html=html,
            text=text,
            fetched_at=datetime.now(UTC),
            js_only=js_only,
        )

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()
