"""Public-LinkedIn loads via Gemini grounded Google Search.

We **never fetch linkedin.com** — the value is the public URL that appears
in Google Search's grounding citations. One grounded call returns a batch
of recent public LinkedIn posts offering freight loads (or seeking trucks)
in the 32-state east-of-Texas region; the returned ``linkedin_url`` is
dropped unless it also appears, modulo ``http↔https`` and trailing-slash,
in ``groundingMetadata.groundingChunks[].web.uri`` citations.

Writes land in ``loads`` with ``source='linkedin'`` and
``source_ref=<post URL>``, deduped by the ``(source, source_ref)`` unique
index. The adapter is a drop-in ``LoadSource`` — ``refresh_source('linkedin')``
and the hourly ``loads.refresh`` tick pick it up without touching the
agent-browser sidecar.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.integrations.adapters.ai.provider import LLMProvider, NullProvider, get_for
from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad

log = logging.getLogger(__name__)


_LINKEDIN_RE = re.compile(
    r"^https?://(?:www\.|[a-z]{2}\.)?linkedin\.com/(?:posts|feed|pulse)/[A-Za-z0-9_%\-/:?=&.;]+$"
)
_STATE_RE = re.compile(r"^[A-Z]{2}$")


# 32 states east of Texas (per repeated user rule, memory-free: Midwest +
# Southeast + Northeast, excluding TX and the north/west column).
IN_REGION = {
    "AL", "AR", "CT", "DC", "DE", "FL", "GA", "IA", "IL", "IN", "KS", "KY",
    "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "NC", "NH", "NJ", "NY",
    "OH", "OK", "PA", "RI", "SC", "TN", "VA", "VT", "WI", "WV",
}


def _normalize_url(u: str) -> str:
    if not u:
        return ""
    v = u.strip().rstrip("/")
    return re.sub(r"^https?://", "://", v).lower()


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


class LinkedInLoadsSource:
    """``LoadSource`` adapter for public LinkedIn load posts (grounded)."""

    kind: str = "linkedin"

    def __init__(self, settings: Any | None = None, *, sessionmaker: Any = None) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker

    def _kill(self, settings) -> bool:
        # Reuse the global loads-agent kill switch — one toggle for every
        # AI-driven loads source keeps the owner's panic button simple.
        return (getattr(settings, "loads_agent_kill", "") or "") == "1"

    def _api_key_set(self, settings) -> bool:
        key = getattr(settings, "gemini_api_key", None)
        try:
            return bool(key and key.get_secret_value())
        except Exception:  # noqa: BLE001
            return False

    @property
    def enabled(self) -> bool:
        s = self._settings
        if s is None:
            return False
        if self._kill(s):
            return False
        return self._api_key_set(s)

    def reason(self) -> str | None:
        s = self._settings
        if s is None:
            return "no_settings"
        if self._kill(s):
            return "agent_killed"
        if not self._api_key_set(s):
            return "no_api_key"
        return None

    async def _grounded(self, provider: LLMProvider, prompt: str) -> tuple[str, list[dict]]:
        call = await provider.search_grounded(prompt)
        if call.status != "ok":
            raise RuntimeError(f"grounded search failed: {call.status} {call.error}")
        return call.text, list(call.citations or [])

    async def _count_runs_today(self) -> int:
        """Count ``agent_runs`` rows for ``source='linkedin'`` on the current
        UTC day — used by the daily cap below. Returns 0 when no sessionmaker
        was passed (unit-test path)."""
        if self._sessionmaker is None:
            return 0
        try:
            from sqlalchemy import func, select

            from app.prospecting.models import AgentRun
            async with self._sessionmaker() as s:
                today = datetime.now(UTC).date()
                row = await s.execute(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(AgentRun.source == "linkedin")
                    .where(func.date(AgentRun.started_at) == today)
                )
                return int(row.scalar() or 0)
        except Exception:  # noqa: BLE001
            return 0

    async def _log_run(self, status: str, loads: int, error: str | None = None) -> None:
        if self._sessionmaker is None:
            return
        try:
            from app.prospecting.models import AgentRun
            async with self._sessionmaker() as s:
                s.add(
                    AgentRun(
                        source="linkedin",
                        ended_at=datetime.now(UTC),
                        status=status,
                        steps=1,
                        tokens_in=0,
                        tokens_out=0,
                        error=error,
                    )
                )
                await s.commit()
        except Exception:  # noqa: BLE001
            return

    async def fetch(self, settings) -> list[RawLoad]:
        if not self.enabled:
            return []
        target = int(getattr(settings, "linkedin_loads_target", 15) or 15)
        # Daily cap — Gemini billed per call; one grounded search per hour
        # tick is enough. Default 24/day (matches the hourly refresh tick);
        # overridable by ``settings.linkedin_loads_daily_cap``.
        daily_cap = int(getattr(settings, "linkedin_loads_daily_cap", 24) or 24)
        if await self._count_runs_today() >= daily_cap:
            await self._log_run("cap_exceeded", 0)
            log.info("linkedin_source: daily cap %d reached", daily_cap)
            return []
        provider = get_for(
            "loads_agent",
            settings=settings,
            ai_features=None,
        )
        if isinstance(provider, NullProvider):
            return []

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        states = ", ".join(sorted(IN_REGION))
        prompt = (
            "Using Google Search, find up to "
            f"{target} CURRENT public LinkedIn posts (less than 7 days old as of {today}) "
            "that OFFER freight loads for a US dry-van / reefer / flatbed truckload carrier, "
            "OR are brokers / shippers asking for a truck. Only posts whose origin OR destination "
            f"is in one of these US states: {states}. "
            "Return STRICT JSON only, no prose, matching this shape:\n"
            '{"loads":[{"linkedin_url":"https://www.linkedin.com/posts/...",'
            '"broker_name":"","origin_city":"","origin_state":"XX",'
            '"dest_city":"","dest_state":"XX","equipment":"",'
            '"rate_usd":null,"miles":null,"pickup_date":"YYYY-MM-DD",'
            '"snippet":""}]}\n'
            "linkedin_url MUST be a public https://www.linkedin.com/posts/... URL that "
            "appears verbatim in the Google search results. Never invent URLs or fields; "
            "leave unknown fields empty / null. State codes MUST be two-letter US codes."
        )
        try:
            text, citations = await self._grounded(provider, prompt)
        except Exception as exc:  # noqa: BLE001
            log.warning("linkedin_source: fetch failed: %s", exc)
            await self._log_run("fetch_failed", 0, error=str(exc)[:500])
            return []

        try:
            payload = _extract_json(text)
        except Exception:  # noqa: BLE001
            log.info("linkedin_source: non-JSON model output: %r", (text or "")[:400])
            await self._log_run("extract_failed", 0, error=(text or "")[:200])
            return []

        allowed = {_normalize_url(c["url"]) for c in citations if c.get("url")}
        out: list[RawLoad] = []
        for row in payload.get("loads", []) or []:
            if not isinstance(row, dict):
                continue
            url = (row.get("linkedin_url") or "").strip()
            if not url or not _LINKEDIN_RE.match(url):
                continue
            if _normalize_url(url) not in allowed:
                # Hallucinated URL — drop it loudly rather than persist a fake.
                log.info("linkedin_source: dropped non-cited url %r", url)
                continue
            o_state = str(row.get("origin_state") or "").strip().upper()
            d_state = str(row.get("dest_state") or "").strip().upper()
            if o_state and not _STATE_RE.match(o_state):
                o_state = ""
            if d_state and not _STATE_RE.match(d_state):
                d_state = ""
            # In-region sanity: at least one leg must be inside the 32-state box.
            if o_state and d_state and o_state not in IN_REGION and d_state not in IN_REGION:
                continue
            try:
                rate = float(row["rate_usd"]) if row.get("rate_usd") not in (None, "") else None
            except (TypeError, ValueError):
                rate = None
            try:
                miles = int(row["miles"]) if row.get("miles") not in (None, "") else None
            except (TypeError, ValueError):
                miles = None
            pickup: datetime | None = None
            try:
                raw_pick = str(row.get("pickup_date") or "")
                if raw_pick:
                    pickup = datetime.fromisoformat(raw_pick).replace(tzinfo=UTC)
            except Exception:  # noqa: BLE001
                pickup = None
            out.append(
                RawLoad(
                    source="linkedin",
                    source_ref=url[:128],
                    broker_name=str(row.get("broker_name") or "")[:255],
                    broker_email=None,
                    broker_phone=None,
                    origin_city=(row.get("origin_city") or None),
                    origin_state=(o_state or None),
                    dest_city=(row.get("dest_city") or None),
                    dest_state=(d_state or None),
                    pickup_date=pickup,
                    equipment=(row.get("equipment") or None),
                    rate_usd=rate,
                    miles=miles,
                    posted_at=datetime.now(UTC),
                    raw={"linkedin_url": url, "snippet": row.get("snippet") or ""},
                )
            )
        await self._log_run("ok", len(out))
        return out

    async def test_connection(self, settings) -> ConnectionTest:
        reason = self.reason()
        if reason:
            return ConnectionTest(ok=False, latency_ms=0, reason=reason, sample_count=0)
        return ConnectionTest(ok=True, latency_ms=0, reason=None, sample_count=0)


__all__ = ["IN_REGION", "LinkedInLoadsSource"]
