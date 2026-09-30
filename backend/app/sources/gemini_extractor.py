"""Text-only Gemini extractor for website contacts.

Given a page's cleaned text (already fetched by `sources.fetcher`), ask Gemini
to lift emails, phones, and named people **verbatim** — the prompt forbids
invention, and the caller runs post-validation (regex + freight-title check) so
even a mis-behaving model can't smuggle in a guessed `first.last@` address.

No Google-Search grounding: the input is the text; we don't want the model
searching the web for more.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.sources.emails import normalize_email
from app.sources.linkedin_search import GEMINI_ENDPOINT
from app.sources.provider import GeminiProvider, LLMProvider, NullProvider

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExtractedEmail:
    value: str
    context: str | None


@dataclass(frozen=True, slots=True)
class ExtractedPhone:
    value: str
    context: str | None


@dataclass(frozen=True, slots=True)
class ExtractedPerson:
    name: str
    title: str
    email: str | None
    phone: str | None


@dataclass(frozen=True, slots=True)
class Extraction:
    status: str  # 'ok' | 'no_api_key' | 'fetch_failed' | 'extract_failed'
    error: str | None
    emails: list[ExtractedEmail] = field(default_factory=list)
    phones: list[ExtractedPhone] = field(default_factory=list)
    people: list[ExtractedPerson] = field(default_factory=list)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def _normalize_phone(v: str | None) -> str | None:
    if not v:
        return None
    digits = re.sub(r"\D+", "", v)
    if len(digits) < 10:
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _title_freight_relevant(title: str, titles_regex: str) -> bool:
    if not title:
        return False
    return bool(re.search(titles_regex, title, re.IGNORECASE))


async def extract_contacts(
    text: str,
    *,
    provider: LLMProvider,
    titles_regex: str,
    timeout_s: float = 30.0,
) -> Extraction:
    """Ask Gemini to lift emails/phones/people from `text`. Verbatim only."""
    if isinstance(provider, NullProvider):
        return Extraction(status="no_api_key", error=None)
    if not text.strip():
        return Extraction(status="ok", error=None)

    assert isinstance(provider, GeminiProvider)
    prompt = (
        "Extract PUBLIC contact information from the WEBSITE TEXT below. "
        "Rules: (1) NEVER invent emails or phones — only lift what appears VERBATIM. "
        "(2) A person is included ONLY if their title matches a freight-relevant role "
        "(logistics, transportation, traffic, warehouse, supply chain, distribution, "
        "shipping, procurement, buyer, operations, VP ops, director of logistics). "
        "(3) Return STRICT JSON only, no prose:\n"
        '{"emails":[{"value":"","context":""}],'
        '"phones":[{"value":"","context":""}],'
        '"people":[{"name":"","title":"","email":"","phone":""}]}\n'
        "WEBSITE TEXT:\n" + text[:40000]
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    url = GEMINI_ENDPOINT.format(model=provider.model)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, params={"key": provider.api_key.get_secret_value()}, json=body)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.info("extractor: fetch failed: %s", exc)
        return Extraction(status="fetch_failed", error=str(exc)[:500])

    out_text = ""
    for cand in (data.get("candidates") or [])[:1]:
        for part in (cand.get("content") or {}).get("parts") or []:
            out_text += part.get("text", "")

    try:
        payload = _extract_json(out_text)
    except Exception as exc:  # noqa: BLE001
        return Extraction(status="extract_failed", error=str(exc)[:500])

    # Validate + dedupe.
    seen_emails: set[str] = set()
    emails: list[ExtractedEmail] = []
    for row in payload.get("emails", []) or []:
        v = normalize_email(row.get("value"))
        if not v or v in seen_emails:
            continue
        # Only accept emails that actually appear (case-insensitive) in the source text.
        if v not in text.lower():
            continue
        seen_emails.add(v)
        emails.append(ExtractedEmail(value=v, context=(row.get("context") or "")[:240]))

    seen_phones: set[str] = set()
    phones: list[ExtractedPhone] = []
    for row in payload.get("phones", []) or []:
        v = _normalize_phone(row.get("value"))
        if not v or v in seen_phones:
            continue
        seen_phones.add(v)
        phones.append(ExtractedPhone(value=v, context=(row.get("context") or "")[:240]))

    people: list[ExtractedPerson] = []
    for row in payload.get("people", []) or []:
        title = (row.get("title") or "").strip()
        if not _title_freight_relevant(title, titles_regex):
            continue
        name = (row.get("name") or "").strip()
        if not name:
            continue
        e = normalize_email(row.get("email"))
        # If the model provided an email, it must appear in the source text too.
        if e and e not in text.lower():
            e = None
        p = _normalize_phone(row.get("phone"))
        people.append(ExtractedPerson(name=name, title=title, email=e, phone=p))

    return Extraction(status="ok", error=None, emails=emails, phones=phones, people=people)
