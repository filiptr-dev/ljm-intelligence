"""Lead scoring via the LLM provider seam.

Historically called Gemini directly; now goes through `get_for("lead_scoring")`
so Settings can flip the provider. Keeps the same `GeminiScorer.score(lead)`
public shape for back-compat (the gemini_stage still constructs one), but the
HTTP path is gone — it's a thin wrapper over `provider.generate_json`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from pydantic import SecretStr

from app.integrations.adapters.ai.provider import LLMProvider, NullProvider, get_for

log = logging.getLogger(__name__)

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


def build_prompt(lead) -> str:
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
    return SCORING_SYSTEM + "\n\nLead:\n" + json.dumps(facts, default=str)


class GeminiScorer:
    """Back-compat scorer; wraps a provider via the seam."""

    def __init__(self, *, api_key: str | None = None, model: str, provider: LLMProvider | None = None) -> None:
        if provider is not None:
            self._provider = provider
        else:
            secret = SecretStr(api_key) if api_key else None
            self._provider = get_for("lead_scoring", api_key=secret, model=model)
        self.model = getattr(self._provider, "model", model)

    async def score(self, lead) -> ScoreResult:
        if isinstance(self._provider, NullProvider):
            raise RuntimeError("no provider key for lead_scoring")  # noqa: TRY004
        prompt = build_prompt(lead)
        call = await self._provider.generate_json(prompt)
        if call.status != "ok":
            raise RuntimeError(f"provider error: {call.status} {call.error}")
        payload = call.parsed if isinstance(call.parsed, dict) else {}
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
            model=call.model or self.model,
        )
