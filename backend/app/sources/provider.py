"""LLM provider seam — the single one-line swap point future adapters plug into.

`LLMProvider` is the interface the enrichment pipeline calls. Two adapters ship
today: `GeminiProvider` (the four enrichment call sites) and `NullProvider`
(used when `GEMINI_API_KEY` is unset — every call returns empty hits with
`status="no_api_key"`, letting the FMCSA pipeline continue without a hard
dependency on a live API key).

`provider.get_for(purpose)` returns the right adapter for a purpose name. The
future `ai-provider-layer-claude` task migrates existing direct-Gemini call
sites through this seam; today it's used only by the enrichment feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    ok: bool
    status: str  # "ok" | "no_api_key"


class LLMProvider(Protocol):
    kind: str
    model: str

    @property
    def status(self) -> ProviderStatus:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True, slots=True)
class GeminiProvider:
    api_key: SecretStr
    model: str
    kind: str = "gemini"

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(ok=True, status="ok")


@dataclass(frozen=True, slots=True)
class NullProvider:
    model: str = "none"
    kind: str = "null"

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(ok=False, status="no_api_key")


def get_for(purpose: str, *, api_key: SecretStr | None, model: str) -> LLMProvider:
    """Return the right provider for `purpose`.

    Today, `purpose` is informational — all enrichment call sites share one
    Gemini config. The parameter is here so the future Claude adapter can
    route a subset (e.g. "extractor" → Claude, "search" → Gemini) without a
    call-site rewrite.
    """
    if api_key is None or not api_key.get_secret_value():
        return NullProvider()
    return GeminiProvider(api_key=api_key, model=model)
