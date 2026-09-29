"""Gemini per-lead scoring — repeatable, cheap, no web grounding."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SCORING_SYSTEM = (
    "You score sales leads for LJM International — a US long-haul dry-van freight brokerage in Lincoln Park, NJ, "
    "serving the eastern US (32 states). Higher score = better fit as a company that BUYS trucking (brokers, "
    "direct shippers, forwarders). Motor carriers themselves score low (they're competitors). Return STRICT JSON: "
    '{"score": 0-100, "rationale": "one short sentence", "signals": ["…","…"]}'
)


@dataclass
class ScoreResult:
    score: int
    rationale: str
    signals: list[str] = field(default_factory=list)
    model: str = ""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


class GeminiScorer:
    def __init__(self, *, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def score(self, lead) -> ScoreResult:  # noqa: ANN001 - accepts either ORM Lead or dict-shape
        facts = {
            "name": getattr(lead, "name", None),
            "kind": getattr(lead, "kind", None),
            "state": getattr(lead, "state", None),
            "city": getattr(lead, "city", None),
            "mc": getattr(lead, "mc", None),
            "dot": getattr(lead, "dot", None),
            "domain": getattr(lead, "domain", None),
            "evidence": getattr(lead, "evidence", None),
        }
        prompt = SCORING_SYSTEM + "\n\nLead:\n" + json.dumps(facts, default=str)
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        url = GEMINI_ENDPOINT.format(model=self.model)
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, params={"key": self.api_key}, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = ""
        candidates = data.get("candidates") or []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
        payload = _extract_json(text)
        raw_score = payload.get("score", 0)
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))
        return ScoreResult(
            score=score,
            rationale=str(payload.get("rationale", ""))[:500],
            signals=[str(s)[:120] for s in (payload.get("signals") or [])][:8],
            model=self.model,
        )
