"""Gemini-grounded LinkedIn decision-maker + company discovery.

Two entry points, both grounded via Google Search. LJM **never fetches
linkedin.com**: the value is the *public URL surfaced by search*, not the
profile body. A `linkedin_url` is dropped unless it appears verbatim
(modulo `http↔https` and trailing slash) in the response's
`groundingMetadata.groundingChunks[].web.uri` citations.

Entry points
------------
- `find_decision_makers(company, *, target_count)` — freight-relevant titles
  (Logistics/Transportation/Traffic/Warehouse/Supply Chain/Distribution/VP
  Supply Chain/VP Operations/Director of Logistics/Shipping/Procurement/Buyer)
  at the company. Returns `DecisionMakerHits`.
- `find_company_page(company)` — the company's public LinkedIn company URL and
  public website URL, both from grounded search results only. Returns
  `CompanyDiscoveryHits`. Used to fill `linkedin_company_url` / `website_url`
  on leads and candidates (especially useful for FMCSA rows with no domain).

All prompts request STRICT JSON; missing/malformed JSON returns empty hits with
`status="extract_failed"`. Anything network-y is logged and returns
`status="fetch_failed"`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.sources.provider import LLMProvider, NullProvider

log = logging.getLogger(__name__)

_LINKEDIN_IN_RE = re.compile(r"^https?://(?:www\.|[a-z]{2}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?$")
_LINKEDIN_COMPANY_RE = re.compile(r"^https?://(?:www\.|[a-z]{2}\.)?linkedin\.com/company/[A-Za-z0-9\-_%]+/?$")

FREIGHT_TITLES = (
    "Logistics Manager",
    "Transportation Manager",
    "Traffic Manager",
    "Warehouse Manager",
    "Supply Chain Manager",
    "Distribution Manager",
    "VP Supply Chain",
    "VP Operations",
    "Director of Logistics",
    "Shipping Manager",
    "Procurement Manager",
    "Buyer",
)


@dataclass(frozen=True, slots=True)
class CompanyRef:
    name: str
    state: str
    kind: str  # 'Shipper' | 'Broker' | 'Forwarder'
    industry_hint: str | None = None
    domain: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionMakerHit:
    name: str
    title: str
    linkedin_url: str
    evidence_url: str | None


@dataclass(frozen=True, slots=True)
class DecisionMakerHits:
    status: str  # 'ok' | 'no_api_key' | 'fetch_failed' | 'extract_failed'
    error: str | None
    people: list[DecisionMakerHit] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CompanyDiscoveryHits:
    status: str  # 'ok' | 'no_api_key' | 'fetch_failed' | 'extract_failed'
    error: str | None
    linkedin_company_url: str | None
    website_url: str | None
    citations: list[dict] = field(default_factory=list)


def _normalize_url(u: str) -> str:
    """http↔https + trailing-slash equivalence for citation matching."""
    if not u:
        return ""
    v = u.strip().rstrip("/")
    return re.sub(r"^https?://", "://", v).lower()


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


async def _grounded_call(provider: LLMProvider, prompt: str, *, timeout_s: float = 30.0) -> tuple[str, list[dict]]:
    """Grounded search through the LLM seam. Returns (text, citations)."""
    call = await provider.search_grounded(prompt)
    if call.status != "ok":
        raise RuntimeError(f"grounded search failed: {call.status} {call.error}")
    return call.text, list(call.citations or [])


async def find_decision_makers(
    company: CompanyRef,
    *,
    provider: LLMProvider,
    target_count: int = 5,
) -> DecisionMakerHits:
    if isinstance(provider, NullProvider):
        return DecisionMakerHits(status="no_api_key", error=None)

    prompt = (
        f"Using Google Search, find up to {target_count} PUBLIC decision-makers at "
        f'"{company.name}" (a US {company.kind} in {company.state}'
        + (f", industry: {company.industry_hint}" if company.industry_hint else "")
        + "). Include ONLY people whose job title matches one of these freight-relevant roles: "
        + ", ".join(FREIGHT_TITLES)
        + ". Return STRICT JSON only, no prose:\n"
        + '{"people":[{"name":"","title":"","linkedin_url":"","evidence_url":""}]}\n'
        + "linkedin_url MUST be a public https://www.linkedin.com/in/... URL that appears in the "
        + "search results. Never invent URLs; leave the field empty if you are not certain."
    )
    try:
        text, citations = await _grounded_call(provider, prompt)
    except Exception as exc:  # noqa: BLE001
        log.info("linkedin_search: fetch failed: %s", exc)
        return DecisionMakerHits(status="fetch_failed", error=str(exc)[:500])

    try:
        payload = _extract_json(text)
    except Exception as exc:  # noqa: BLE001
        log.info("linkedin_search: extract failed: %r", text[:400])
        return DecisionMakerHits(status="extract_failed", error=str(exc)[:500], citations=citations)

    # Citation-anchored URL validation.
    allowed = {_normalize_url(c["url"]) for c in citations if c.get("url")}
    people: list[DecisionMakerHit] = []
    for row in payload.get("people", []) or []:
        url = (row.get("linkedin_url") or "").strip()
        if not url or not _LINKEDIN_IN_RE.match(url):
            log.info("linkedin_search: dropped bad url %r", url)
            continue
        if _normalize_url(url) not in allowed:
            log.info("linkedin_search: dropped hallucinated url %r (not in citations)", url)
            continue
        name = (row.get("name") or "").strip()
        title = (row.get("title") or "").strip()
        if not name or not title:
            continue
        people.append(
            DecisionMakerHit(
                name=name,
                title=title,
                linkedin_url=url,
                evidence_url=(row.get("evidence_url") or "").strip() or None,
            )
        )
    return DecisionMakerHits(status="ok", error=None, people=people, citations=citations)


async def find_company_page(
    company: CompanyRef,
    *,
    provider: LLMProvider,
) -> CompanyDiscoveryHits:
    """Discover the company's LinkedIn company page + public website via grounded search.

    Same citation-anchored rule: any URL returned that does not appear in the
    search-result citations is dropped. Especially useful for FMCSA rows with
    no `domain` column — the discovered `website_url` becomes the input for
    the site-scraper stage.
    """
    if isinstance(provider, NullProvider):
        return CompanyDiscoveryHits(status="no_api_key", error=None, linkedin_company_url=None, website_url=None)

    prompt = (
        f"Using Google Search, find the OFFICIAL LinkedIn company page and public website for "
        f'"{company.name}" (a US {company.kind} in {company.state}). '
        f"Return STRICT JSON only, no prose:\n"
        '{"linkedin_company_url":"","website_url":""}\n'
        "linkedin_company_url MUST be a https://www.linkedin.com/company/... URL that appears "
        "in the search results. website_url MUST be a URL that appears in the search results. "
        "Never invent URLs; leave a field empty if you are not certain."
    )
    try:
        text, citations = await _grounded_call(provider, prompt)
    except Exception as exc:  # noqa: BLE001
        log.info("company_discovery: fetch failed: %s", exc)
        return CompanyDiscoveryHits(
            status="fetch_failed", error=str(exc)[:500], linkedin_company_url=None, website_url=None
        )

    try:
        payload = _extract_json(text)
    except Exception as exc:  # noqa: BLE001
        return CompanyDiscoveryHits(
            status="extract_failed",
            error=str(exc)[:500],
            linkedin_company_url=None,
            website_url=None,
            citations=citations,
        )

    allowed = {_normalize_url(c["url"]) for c in citations if c.get("url")}
    li = (payload.get("linkedin_company_url") or "").strip()
    site = (payload.get("website_url") or "").strip()

    li_ok: str | None = None
    if li and _LINKEDIN_COMPANY_RE.match(li) and _normalize_url(li) in allowed:
        li_ok = li

    site_ok: str | None = None
    if (
        site
        and site.startswith(("http://", "https://"))
        and _normalize_url(site) in allowed
        and "linkedin.com" not in site.lower()  # never accept linkedin.com as a website URL
    ):
        site_ok = site

    return CompanyDiscoveryHits(
        status="ok",
        error=None,
        linkedin_company_url=li_ok,
        website_url=site_ok,
        citations=citations,
    )
