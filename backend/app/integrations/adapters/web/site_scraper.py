"""Site scraper — orchestrates the website-contacts pass for one company.

Steps
-----
1. Enumerate candidate URLs from the company's domain: home + `contact`,
   `contact-us`, `about`, `about-us`, `team`, `our-team`, `people`, plus any
   anchor `href` on the homepage matching those slugs. Hard-cap `page_cap` pages
   per company across the whole pass.
2. For each URL:
     - `robots.is_allowed` → skip if disallowed;
     - else `fetcher.fetch`;
     - `asyncio.sleep(between_requests_s)` between requests.
3. Hand the concatenated cleaned text (truncated 40 KB) to
   `gemini_extractor.extract_contacts()`.
4. Return `SiteContactsHits(emails, phones, people, pages_fetched,
   js_only_flags, status, error, page_provenance)`.

The scraper never touches linkedin.com; the plan's "Never fetch LinkedIn" rule
is enforced by simply refusing any candidate URL whose host contains
`linkedin.com`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from app.integrations.adapters.ai.gemini_extractor import Extraction, extract_contacts
from app.integrations.adapters.ai.provider import LLMProvider
from app.shared.fetcher import LJM_USER_AGENT, Fetcher, FetchResult
from app.shared.robots import is_allowed

log = logging.getLogger(__name__)

CONTACT_SLUGS = ("contact", "contact-us", "about", "about-us", "team", "our-team", "people")


@dataclass(frozen=True, slots=True)
class PageProvenance:
    url: str
    final_url: str
    status: int
    js_only: bool


@dataclass(frozen=True, slots=True)
class SiteContactsHits:
    status: str  # 'ok' | 'no_api_key' | 'fetch_failed' | 'extract_failed' | 'robots_blocked' | 'js_only' | 'skipped'
    error: str | None
    extraction: Extraction | None
    pages_fetched: int
    js_only_flags: int
    page_provenance: list[PageProvenance] = field(default_factory=list)
    # Combined cleaned text (truncated) — used downstream by fit_score's
    # text-signal extractor. Empty on failure paths.
    combined_text: str = ""


def _candidate_urls(domain: str) -> list[str]:
    d = domain.strip().lower().removeprefix("http://").removeprefix("https://").removeprefix("www.").rstrip("/")
    if not d:
        return []
    base = f"https://{d}"
    urls = [base + "/"]
    for slug in CONTACT_SLUGS:
        urls.append(f"{base}/{slug}")
    return urls


_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _extra_urls_from_home(home_html: str, base_url: str) -> list[str]:
    """Follow homepage anchors that point at contact/about/team slugs."""
    if not home_html:
        return []
    extras: list[str] = []
    seen: set[str] = set()
    for m in _HREF_RE.finditer(home_html):
        href = m.group(1).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if "linkedin.com" in (parts.hostname or ""):
            continue
        path = (parts.path or "").lower()
        if any(slug in path for slug in CONTACT_SLUGS):
            key = absolute.split("#", 1)[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            extras.append(absolute)
    return extras


async def scrape_site(
    domain: str,
    *,
    fetcher: Fetcher,
    provider: LLMProvider,
    titles_regex: str,
    page_cap: int = 8,
    between_requests_s: float = 1.5,
) -> SiteContactsHits:
    """Fetch up to `page_cap` pages on `domain`, extract, and return hits."""
    urls = _candidate_urls(domain)
    if not urls:
        return SiteContactsHits(status="skipped", error="no domain", extraction=None, pages_fetched=0, js_only_flags=0)

    fetched: list[FetchResult] = []
    provenance: list[PageProvenance] = []
    seen_urls: set[str] = set()

    # 1) home
    home_url = urls[0]
    if is_allowed(home_url, LJM_USER_AGENT):
        r = await fetcher.fetch(home_url)
        seen_urls.add(home_url)
        fetched.append(r)
        provenance.append(PageProvenance(url=home_url, final_url=r.final_url, status=r.status, js_only=r.js_only))

    # 2) merge homepage anchor hrefs into the queue
    if fetched:
        for extra in _extra_urls_from_home(fetched[0].html, home_url):
            if extra not in urls:
                urls.append(extra)

    # 3) walk the rest, cap total pages
    for url in urls[1:]:
        if len(fetched) >= page_cap:
            break
        if url in seen_urls:
            continue
        seen_urls.add(url)
        if not is_allowed(url, LJM_USER_AGENT):
            continue
        if between_requests_s > 0:
            await asyncio.sleep(between_requests_s)
        r = await fetcher.fetch(url)
        fetched.append(r)
        provenance.append(PageProvenance(url=url, final_url=r.final_url, status=r.status, js_only=r.js_only))

    if not fetched:
        return SiteContactsHits(status="robots_blocked", error=None, extraction=None, pages_fetched=0, js_only_flags=0)

    # 4) aggregate + extract
    combined = "\n\n".join(f.text for f in fetched if f.text)[:40000]
    js_only_flags = sum(1 for f in fetched if f.js_only)

    if not combined:
        # Nothing extractable — likely a JS-only site.
        if js_only_flags > 0:
            return SiteContactsHits(
                status="js_only",
                error=None,
                extraction=None,
                pages_fetched=len(fetched),
                js_only_flags=js_only_flags,
                page_provenance=provenance,
            )
        return SiteContactsHits(
            status="fetch_failed",
            error="empty text",
            extraction=None,
            pages_fetched=len(fetched),
            js_only_flags=js_only_flags,
            page_provenance=provenance,
        )

    extraction = await extract_contacts(combined, provider=provider, titles_regex=titles_regex)
    return SiteContactsHits(
        status=extraction.status,
        error=extraction.error,
        extraction=extraction,
        pages_fetched=len(fetched),
        js_only_flags=js_only_flags,
        page_provenance=provenance,
        combined_text=combined,
    )
