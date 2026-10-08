"""Gemini discovery with Google Search grounding.

Contract: given LJM's ICP + the 32-state region, ask Gemini for `target_count` companies
that buy trucking (freight brokers, direct shippers, forwarders). Structured JSON output,
plus `citations` from the grounding metadata.

Why the HTTP path (not google-genai SDK): keeping the dep footprint tiny on Render free, and
the REST body for `tools: [{google_search: {}}]` is stable and documented. If the SDK story
firms up we can swap in `from google import genai` here without touching callers.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.shared.region import IN_REGION_STATES
from app.sources.emails import normalize_email
from app.integrations.adapters.ai.provider import GeminiProvider, LLMProvider, NullProvider

log = logging.getLogger(__name__)

LJM_PROFILE = (
    "LJM International is a US freight brokerage/carrier in Lincoln Park, NJ. "
    "Long-haul dry van, east of the Mississippi. Target customers: freight brokers, direct shippers "
    "(retailers, food & beverage, manufacturers, building materials, paper & packaging, chemicals, "
    "steel/metals, agriculture, automotive parts), and forwarders / 3PLs, in these US states: "
    + ", ".join(sorted(IN_REGION_STATES))
    + "."
)

DISCOVERY_PROMPT = (
    "You are helping LJM International find companies to reach out to. "
    f"{LJM_PROFILE} "
    "Using Google Search grounding, list {n} distinct US companies (freight brokers, direct shippers, "
    "or forwarders/3PLs) that recently show signs of shipping freight in-region. "
    "Exclude motor carriers themselves (companies that OWN and operate trucks for hire). "
    "For each company return: name, domain (canonical, no https), 2-letter US state (in-region), "
    "city, kind (Broker|Shipper|Forwarder), the public contact email if one is published on "
    "their own website (else an empty string, never guess), one short why-fit sentence. "
    "Return STRICT JSON only, no prose, in this shape:\n"
    '{"companies":[{"name":"","domain":"","state":"","city":"","kind":"","email":"","why":""}]}'
)


@dataclass
class DiscoveredCompany:
    id: str
    mc: str | None
    dot: str | None
    domain: str | None
    name: str
    kind: str
    state: str
    city: str | None
    primary_email: str | None = None
    citations: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _extract_json(text: str) -> dict:
    """Model may wrap JSON in ```json fences or add leading text — extract the first {...} block."""
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


class GeminiDiscoverer:
    """Grounded shipper-discovery, now routed through the LLM provider seam.

    Keeps the historical `(api_key, model)` constructor signature so the
    gemini_stage call site is unchanged. A caller that wants Claude (never, in
    v1 — Claude has no grounded search) can pass a pre-built `provider=`.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        provider: LLMProvider | None = None,
    ) -> None:
        if provider is not None:
            self._provider = provider
        elif api_key:
            self._provider = GeminiProvider(api_key=SecretStr(api_key), model=model, timeout_s=60.0)
        else:
            self._provider = NullProvider(model=model)
        self.model = getattr(self._provider, "model", model)

    async def discover(self, *, target_count: int = 8) -> list[DiscoveredCompany]:
        if isinstance(self._provider, NullProvider):
            return []
        prompt = DISCOVERY_PROMPT.replace("{n}", str(target_count))
        call = await self._provider.search_grounded(prompt)
        if call.status != "ok":
            log.warning("gemini discovery failed: status=%s err=%s", call.status, call.error)
            raise RuntimeError(f"{call.status}: {call.error or ''}")
        text = call.text
        try:
            payload = _extract_json(text)
        except Exception:  # noqa: BLE001 — any malformed LLM payload short-circuits to []
            log.warning("gemini discovery: could not parse JSON, raw text=%r", text[:400])
            return []

        citations = list(call.citations or [])

        out: list[DiscoveredCompany] = []
        seen_domains: set[str] = set()
        for row in payload.get("companies", [])[:target_count]:
            state = (row.get("state") or "").upper().strip()
            if state not in IN_REGION_STATES:
                continue
            kind = (row.get("kind") or "").strip().title()
            if kind not in ("Broker", "Shipper", "Forwarder"):
                continue
            domain = (row.get("domain") or "").strip().lower().rstrip("/").removeprefix("www.")
            if not domain:
                continue
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            out.append(
                DiscoveredCompany(
                    id=f"DOMAIN-{domain}",
                    mc=None,
                    dot=None,
                    domain=domain,
                    name=(row.get("name") or domain).strip(),
                    kind=kind,
                    state=state,
                    city=(row.get("city") or "").strip() or None,
                    primary_email=normalize_email(row.get("email")),
                    citations=citations,
                    raw={"why": row.get("why"), "model_row": row},
                )
            )
        log.info("gemini discovery: %d companies (in-region, deduped)", len(out))
        return out
