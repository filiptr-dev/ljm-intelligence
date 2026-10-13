"""LLM provider seam — single swap point for Gemini + Claude adapters.

Grown from the enrichment-only seam into the full `ai-provider-layer-claude`
contract. Every AI feature (`email_drafts`, `lead_scoring`, `shipper_discovery`,
`enrichment_extractor`, `inbox_analysis`) now goes through `get_for(feature)`
and calls one of three verbs:

    generate_json(prompt, *, schema_hint=None) -> ProviderCall
    generate_text(prompt)                      -> ProviderCall
    search_grounded(prompt)                    -> ProviderCall   # optional

`search_grounded` raises `CapabilityUnavailable` on adapters that cannot do
grounded web search (Claude today). The three text/JSON verbs work on both
providers; `shipper_discovery` sticks to Gemini.

Keys are env-only. If the picked provider's key is missing, `get_for` returns
`NullProvider` and callers see `status="no_api_key"` on every ProviderCall —
no 500s, the daily crawl keeps running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx
from pydantic import SecretStr

log = logging.getLogger(__name__)


# --- Provider / model registry ---------------------------------------------

ALLOWED_MODELS: dict[str, list[str]] = {
    "gemini": ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.5-pro"],
    "claude": ["claude-sonnet-5-5", "claude-haiku-4-5-20251001"],
}

# USD per 1M tokens. Approximate list prices — a usage-log cost estimate, not
# a billing contract. Keep conservative: when a model is missing we fall back
# to the cheapest row for its provider so the number is non-zero but low.
PRICES: dict[tuple[str, str], tuple[Decimal, Decimal]] = {
    ("gemini", "gemini-3.5-flash-lite"): (Decimal("0.10"), Decimal("0.40")),
    ("gemini", "gemini-3.5-flash"): (Decimal("0.30"), Decimal("2.50")),
    ("gemini", "gemini-3.5-pro"): (Decimal("1.25"), Decimal("10.00")),
    ("claude", "claude-sonnet-5-5"): (Decimal("3.00"), Decimal("15.00")),
    ("claude", "claude-haiku-4-5-20251001"): (Decimal("1.00"), Decimal("5.00")),
}

DEFAULT_FEATURES: dict[str, dict[str, str]] = {
    "email_drafts": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    "inbox_analysis": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    # Pre-fill the "Reply" composer on an inbox thread. Cheap-flash by default
    # — the operator always edits before sending, so hallucinations are
    # self-correcting; a mis-firing timeout must never block the UI.
    "inbox_draft_reply": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    "lead_scoring": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    "enrichment_extractor": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    "shipper_discovery": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    # Headless-agent tool loop (plan 2026-10-08-loads-aggregator-headless-agent).
    # Cheap-flash by default — the loop is step-bounded and the output is
    # structured (`{"tool":..., "args":...}`), hallucinations show up as
    # `unknown_tool` errors not silent corruption.
    "loads_agent": {"provider": "gemini", "model": "gemini-3.5-flash"},
}

FEATURE_NAMES: tuple[str, ...] = tuple(DEFAULT_FEATURES.keys())


# --- Data types -------------------------------------------------------------


class CapabilityUnavailable(RuntimeError):
    """Raised when an adapter cannot satisfy an optional capability (grounded search on Claude)."""


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    ok: bool
    status: str  # "ok" | "no_api_key"


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """One adapter call. Every verb returns one of these so the usage-log is uniform."""

    text: str
    parsed: Any | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    model: str
    provider: str
    status: str  # "ok" | "no_api_key" | "error" | "timeout" | "rate_limited"
    error: str | None = None
    citations: list[dict] = field(default_factory=list)


def _price(provider: str, model: str, in_tok: int, out_tok: int) -> Decimal:
    rates = PRICES.get((provider, model))
    if rates is None and ALLOWED_MODELS.get(provider):
        rates = PRICES.get((provider, ALLOWED_MODELS[provider][0]))
    if rates is None:
        return Decimal(0)
    in_rate, out_rate = rates
    million = Decimal(1000000)
    return (Decimal(in_tok) * in_rate + Decimal(out_tok) * out_rate) / million


def _classify_error(exc: Exception) -> str:
    """Map a raised exception to one of the ProviderCall ``status`` strings.

    The fallback chain reads this value: ``timeout`` / ``rate_limited`` /
    ``server_error`` / ``model_not_found`` → retry on the next provider;
    ``bad_request`` (400 or validation) → stop (a malformed prompt would
    fail everywhere, so trying Claude after Gemini says 400 just burns
    quota).
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in text:
        return "timeout"
    if any(k in text for k in ("rate limit", "rate-limit", "resource_exhausted", "429", "quota")):
        return "rate_limited"
    # httpx HTTPStatusError prints "Server error '500 Internal Server Error' for url …".
    if "500 " in text or "502 " in text or "503 " in text or "504 " in text:
        return "server_error"
    if "404" in text and ("model" in text or "not found" in text):
        return "model_not_found"
    if "400 " in text or "422 " in text or "validation" in text or "bad request" in text:
        return "bad_request"
    return "error"


# Statuses the fallback chain treats as "the next model might answer this".
# ``bad_request`` is deliberately NOT in here (bad prompt would fail
# everywhere); plain ``error`` is also non-retryable so a programming bug
# (e.g. missing SDK) shows up loudly instead of silently rotating keys.
_RETRYABLE_FALLBACK = frozenset({"timeout", "rate_limited", "server_error", "model_not_found"})


# Module-level trace: feature → "provider:model" of the model that produced
# the last successful answer. In-memory is fine (per the task brief) — the
# ``/ai/features`` endpoint exposes it so the Settings panel can show
# "Last answered by". Reset when the process restarts.
_LAST_ANSWERED_BY: dict[str, str] = {}


def record_answered_by(feature: str, provider: str, model: str) -> None:
    _LAST_ANSWERED_BY[feature] = f"{provider}:{model}"


def last_answered_by() -> dict[str, str]:
    return dict(_LAST_ANSWERED_BY)


def _extract_json(text: str) -> Any:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


# --- Gemini -----------------------------------------------------------------

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _pull_gemini_citations(candidates: list[dict]) -> list[dict]:
    out: list[dict] = []
    if not candidates:
        return out
    gm = candidates[0].get("groundingMetadata") or {}
    for chunk in gm.get("groundingChunks", []) or []:
        web = chunk.get("web") or {}
        if "uri" in web:
            out.append({"url": web["uri"], "title": web.get("title")})
    return out


@dataclass(frozen=True, slots=True)
class GeminiProvider:
    api_key: SecretStr
    model: str
    kind: str = "gemini"
    timeout_s: float = 30.0

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(ok=True, status="ok")

    async def _call(self, body: dict) -> ProviderCall:
        url = GEMINI_ENDPOINT.format(model=self.model)
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(url, params={"key": self.api_key.get_secret_value()}, json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            latency = int((time.perf_counter() - t0) * 1000)
            return ProviderCall(
                text="",
                parsed=None,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency,
                cost_usd=Decimal(0),
                model=self.model,
                provider="gemini",
                status=_classify_error(exc),
                error=str(exc)[:500],
            )
        latency = int((time.perf_counter() - t0) * 1000)
        text = ""
        candidates = data.get("candidates") or []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata") or {}
        in_tok = int(usage.get("promptTokenCount", 0) or 0)
        out_tok = int(usage.get("candidatesTokenCount", 0) or 0)
        parsed: Any | None = None
        try:
            parsed = _extract_json(text)
        except Exception:  # noqa: BLE001 — JSON parse is best-effort
            parsed = None
        return ProviderCall(
            text=text,
            parsed=parsed,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency,
            cost_usd=_price("gemini", self.model, in_tok, out_tok),
            model=self.model,
            provider="gemini",
            status="ok",
            error=None,
            citations=_pull_gemini_citations(candidates),
        )

    async def generate_json(self, prompt: str, *, schema_hint: str | None = None) -> ProviderCall:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        return await self._call(body)

    async def generate_text(self, prompt: str) -> ProviderCall:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.5, "responseMimeType": "text/plain"},
        }
        return await self._call(body)

    async def search_grounded(self, prompt: str) -> ProviderCall:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "text/plain"},
        }
        return await self._call(body)


# --- Claude -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaudeProvider:
    api_key: SecretStr
    model: str
    kind: str = "claude"
    timeout_s: float = 30.0
    max_tokens: int = 2048

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(ok=True, status="ok")

    async def _messages(self, prompt: str, *, system: str | None = None) -> ProviderCall:
        # Import inside so modules that only need NullProvider don't force the dep.
        try:
            from anthropic import AsyncAnthropic
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            return ProviderCall(
                text="",
                parsed=None,
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                cost_usd=Decimal(0),
                model=self.model,
                provider="claude",
                status="error",
                error=f"anthropic SDK not installed: {exc}",
            )

        client = AsyncAnthropic(api_key=self.api_key.get_secret_value(), timeout=self.timeout_s)
        t0 = time.perf_counter()
        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            resp = await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            latency = int((time.perf_counter() - t0) * 1000)
            return ProviderCall(
                text="",
                parsed=None,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency,
                cost_usd=Decimal(0),
                model=self.model,
                provider="claude",
                status=_classify_error(exc),
                error=str(exc)[:500],
            )
        latency = int((time.perf_counter() - t0) * 1000)
        text = ""
        content = getattr(resp, "content", None) or []
        for block in content:
            btxt = getattr(block, "text", None)
            if isinstance(btxt, str):
                text += btxt
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        return ProviderCall(
            text=text,
            parsed=None,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency,
            cost_usd=_price("claude", self.model, in_tok, out_tok),
            model=self.model,
            provider="claude",
            status="ok",
            error=None,
        )

    async def generate_text(self, prompt: str) -> ProviderCall:
        return await self._messages(prompt)

    async def generate_json(self, prompt: str, *, schema_hint: str | None = None) -> ProviderCall:
        system = "You respond with ONE strict JSON object and nothing else. No prose, no code fences, no commentary."
        call = await self._messages(prompt, system=system)
        if call.status != "ok" or not call.text:
            return call
        try:
            parsed = _extract_json(call.text)
        except Exception:  # noqa: BLE001
            parsed = None
        return ProviderCall(
            text=call.text,
            parsed=parsed,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            model=call.model,
            provider=call.provider,
            status=call.status,
            error=call.error,
        )

    async def search_grounded(self, prompt: str) -> ProviderCall:
        raise CapabilityUnavailable("Claude web search is not wired in v1 — use Gemini for grounded discovery.")


# --- Null -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NullProvider:
    model: str = "none"
    kind: str = "null"

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(ok=False, status="no_api_key")

    def _call(self) -> ProviderCall:
        return ProviderCall(
            text="",
            parsed=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            cost_usd=Decimal(0),
            model=self.model,
            provider="null",
            status="no_api_key",
            error=None,
        )

    async def generate_json(self, prompt: str, *, schema_hint: str | None = None) -> ProviderCall:
        return self._call()

    async def generate_text(self, prompt: str) -> ProviderCall:
        return self._call()

    async def search_grounded(self, prompt: str) -> ProviderCall:
        return self._call()


class LLMProvider(Protocol):
    kind: str
    model: str

    @property
    def status(self) -> ProviderStatus:  # pragma: no cover
        ...

    async def generate_json(self, prompt: str, *, schema_hint: str | None = None) -> ProviderCall: ...
    async def generate_text(self, prompt: str) -> ProviderCall: ...
    async def search_grounded(self, prompt: str) -> ProviderCall: ...


# --- Fallback chain ---------------------------------------------------------


@dataclass(slots=False)
class FallbackProvider:
    """Ordered chain of adapters: primary first, then zero or more fallbacks.

    Each verb tries the primary; on a retryable ``ProviderCall.status``
    (``timeout`` / ``rate_limited`` / ``server_error`` / ``model_not_found``,
    see ``_RETRYABLE_FALLBACK``) it moves to the next provider. A
    ``bad_request`` / ``no_api_key`` / plain ``error`` is non-retryable:
    ``bad_request`` would fail everywhere (same prompt), ``no_api_key``
    means the primary is a ``NullProvider`` so we SKIP straight to the next
    without logging as a real failure, and plain ``error`` surfaces a bug
    (e.g. missing SDK) rather than silently rotating.

    When every adapter is exhausted the LAST ProviderCall is returned —
    the caller still sees an honest status (``no_api_key`` /
    ``rate_limited`` / …) and never a 500.
    """

    providers: list[LLMProvider]
    feature: str = ""
    # Match the LLMProvider Protocol surface so static callers (`provider.model`,
    # `provider.kind`) keep working — they see the primary's identity.
    kind: str = "fallback"

    @property
    def model(self) -> str:  # delegated to the primary for logging compat
        return self.providers[0].model if self.providers else "none"

    @property
    def status(self) -> ProviderStatus:
        # "ok" if ANY provider has a key — the chain can answer. All-null → "no_api_key".
        for p in self.providers:
            if getattr(p, "status", ProviderStatus(ok=False, status="no_api_key")).ok:
                return ProviderStatus(ok=True, status="ok")
        return ProviderStatus(ok=False, status="no_api_key")

    async def _run(self, verb: str, prompt: str, **kw) -> ProviderCall:
        last: ProviderCall | None = None
        for p in self.providers:
            try:
                method = getattr(p, verb)
                call: ProviderCall = await method(prompt, **kw)
            except CapabilityUnavailable as exc:
                # e.g. Claude asked for search_grounded. Not a failure of the
                # chain; just skip to the next adapter that can.
                log.info("ai.fallback.skip feature=%s provider=%s reason=capability:%s", self.feature, getattr(p, "kind", "?"), exc)
                continue
            last = call
            if call.status == "ok":
                record_answered_by(self.feature or "unknown", call.provider, call.model)
                log.info("ai.fallback.answered feature=%s provider=%s model=%s", self.feature, call.provider, call.model)
                return call
            if call.status == "no_api_key":
                # Not a real failure — just means this slot isn't configured.
                log.debug("ai.fallback.skip feature=%s provider=%s reason=no_api_key", self.feature, getattr(p, "kind", "?"))
                continue
            if call.status not in _RETRYABLE_FALLBACK:
                # bad_request / plain error → stop. Trying again on the same
                # prompt won't help, and this is how a programming bug stays
                # loud.
                log.info("ai.fallback.stop feature=%s provider=%s status=%s", self.feature, call.provider, call.status)
                return call
            log.info(
                "ai.fallback.retry feature=%s provider=%s model=%s status=%s",
                self.feature, call.provider, call.model, call.status,
            )
        return last if last is not None else NullProvider()._call()

    async def generate_json(self, prompt: str, *, schema_hint: str | None = None) -> ProviderCall:
        return await self._run("generate_json", prompt, schema_hint=schema_hint)

    async def generate_text(self, prompt: str) -> ProviderCall:
        return await self._run("generate_text", prompt)

    async def search_grounded(self, prompt: str) -> ProviderCall:
        return await self._run("search_grounded", prompt)


def _parse_fallback_chain(csv: str) -> list[tuple[str, str]]:
    """Turn ``gemini:gemini-3.5-flash,claude:claude-sonnet-5-5`` into
    ``[("gemini", "gemini-3.5-flash"), ("claude", "claude-sonnet-5-5")]``.
    Unknown provider or model entries are silently dropped so a typo in env
    can't blow up every AI call."""
    out: list[tuple[str, str]] = []
    for raw in (csv or "").split(","):
        s = raw.strip()
        if not s or ":" not in s:
            continue
        prov, _, mdl = s.partition(":")
        prov = prov.strip().lower()
        mdl = mdl.strip()
        if prov not in ALLOWED_MODELS or mdl not in ALLOWED_MODELS.get(prov, []):
            continue
        out.append((prov, mdl))
    return out


def _build_single(provider_name: str, model_name: str, settings: Any) -> LLMProvider:
    if provider_name == "gemini":
        key = getattr(settings, "gemini_api_key", None)
        if key is None or not key.get_secret_value():
            return NullProvider(model=model_name)
        return GeminiProvider(api_key=key, model=model_name)
    if provider_name == "claude":
        key = getattr(settings, "anthropic_api_key", None)
        if key is None or not key.get_secret_value():
            return NullProvider(model=model_name)
        return ClaudeProvider(api_key=key, model=model_name)
    return NullProvider(model=model_name)


def resolve_fallback_order(settings: Any, primary: tuple[str, str]) -> list[tuple[str, str]]:
    """Primary + de-duplicated configured fallbacks."""
    chain: list[tuple[str, str]] = [primary]
    seen = {primary}
    for entry in _parse_fallback_chain(getattr(settings, "ai_fallback_models", "") or ""):
        if entry in seen:
            continue
        chain.append(entry)
        seen.add(entry)
    return chain


# --- Routing ----------------------------------------------------------------


def _resolve_feature_choice(feature: str, overrides: dict[str, dict[str, str]] | None) -> dict[str, str]:
    default = DEFAULT_FEATURES.get(feature) or {"provider": "gemini", "model": "gemini-3.5-flash-lite"}
    if overrides and isinstance(overrides.get(feature), dict):
        choice = overrides[feature]
        provider = str(choice.get("provider") or default["provider"])
        model = str(choice.get("model") or default["model"])
        if provider not in ALLOWED_MODELS or model not in ALLOWED_MODELS.get(provider, []):
            return default
        return {"provider": provider, "model": model}
    return default


def get_for(
    feature: str,
    *,
    settings: Any | None = None,
    ai_features: dict[str, dict[str, str]] | None = None,
    # Legacy call-site shim — the enrichment orchestrator still passes these two.
    # Present AND no settings/ai_features → force a Gemini pick.
    api_key: SecretStr | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Pick the right adapter for `feature`.

    Resolution:
      1. If `ai_features` is given, honour the (provider, model) override.
      2. Else fall back to `DEFAULT_FEATURES[feature]`.
      3. Resolve the picked provider's API key from `settings`; missing → NullProvider.
    """
    # Legacy enrichment call site compatibility.
    if settings is None and ai_features is None and (api_key is not None or model is not None):
        if api_key is None or not api_key.get_secret_value():
            return NullProvider()
        return GeminiProvider(api_key=api_key, model=model or "gemini-3.5-flash-lite")

    choice = _resolve_feature_choice(feature, ai_features)
    provider_name = choice["provider"]
    model_name = choice["model"]

    if settings is None:
        return NullProvider(model=model_name)

    # Build the full chain: primary + configured fallbacks. Even a single
    # provider goes through FallbackProvider so the "last answered by" trace
    # is uniform across every feature.
    chain = resolve_fallback_order(settings, (provider_name, model_name))
    adapters = [_build_single(p, m, settings) for (p, m) in chain]
    return FallbackProvider(providers=adapters, feature=feature)
