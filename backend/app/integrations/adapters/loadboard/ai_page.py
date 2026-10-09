"""AI-pages load source — public broker boards via the headless agent.

Reads ``settings.load_source_urls`` (list of ``{label, url, enabled}``,
owner-managed in Settings — stored on the ``settings`` row by migration
0025). Each enabled URL is one agent run with that single URL as the
allowlist. Serialised one-at-a-time to keep host pressure bounded.

``enabled`` iff the agent-browser sidecar URL is set AND at least one URL
is enabled AND the global kill switch is off. Missing env / empty list →
cleanly disabled; ``fetch()`` returns ``[]``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.integrations.adapters.loadboard.agent_browser import AgentSource
from app.integrations.adapters.loadboard.agent_browser.config import resolve as resolve_agent_browser
from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad

log = logging.getLogger(__name__)


class AiPageSource:
    kind: str = "ai_page"

    def __init__(self, settings: Any | None = None, *, sessionmaker: Any = None) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker

    def _urls(self, settings) -> list[dict]:
        raw = getattr(settings, "load_source_urls", None) or []
        if not isinstance(raw, list):
            return []
        return [u for u in raw if isinstance(u, dict) and u.get("enabled") and u.get("url")]

    def _kill(self, settings) -> bool:
        return (getattr(settings, "loads_agent_kill", "") or "") == "1"

    def _agent_url(self, settings) -> str:
        # Env fast path first, then the in-process DB overlay primed at
        # lifespan start / refreshed by the settings PUT.
        env_url = (getattr(settings, "agent_browser_url", "") or "").strip()
        if env_url:
            return env_url
        # Local import avoids a circular import on module load.
        from app.integrations.adapters.loadboard.registry import db_overlay_agent_browser_url
        return db_overlay_agent_browser_url()

    @property
    def enabled(self) -> bool:
        settings = self._settings
        if settings is None:
            return False
        if self._kill(settings):
            return False
        if not self._agent_url(settings):
            return False
        return len(self._urls(settings)) > 0

    def reason(self) -> str | None:
        settings = self._settings
        if settings is None:
            return "no_settings"
        if self._kill(settings):
            return "agent_killed"
        if not self._agent_url(settings):
            return "agent_browser_url_unset"
        if not self._urls(settings):
            return "no_source_urls"
        return None

    async def fetch(self, settings) -> list[RawLoad]:
        if not self._agent_url(settings):
            return []
        if self._kill(settings):
            return []
        urls = self._urls(settings)
        if not urls:
            return []
        out: list[RawLoad] = []
        for entry in urls:
            url = str(entry.get("url") or "")
            if not url:
                continue
            agent = AgentSource(
                src="broker_page", start_url=url, allowlist=[url],
                sessionmaker=self._sessionmaker,
            )
            try:
                raws = await agent.fetch(settings)
            except Exception as exc:  # noqa: BLE001
                log.warning("ai_page/fetch failed %s: %s", url, exc)
                continue
            tag = hashlib.sha256(url.encode()).hexdigest()[:12]
            for r in raws:
                # Overwrite the agent's `broker_page` tag with our own kind.
                r.source = "ai_page"
                if not r.source_ref:
                    r.source_ref = f"url:{tag}"
            out.extend(raws)
        return out

    async def test_connection(self, settings) -> ConnectionTest:
        reason = self.reason()
        if reason:
            return ConnectionTest(ok=False, latency_ms=0, reason=reason, sample_count=0)
        return ConnectionTest(ok=True, latency_ms=0, reason=None, sample_count=0)


class BrokerPageSource(AiPageSource):
    """Alias of :class:`AiPageSource` with ``kind="broker_page"``.

    The hourly GH workflow (``.github/workflows/loads-agent.yml``) refreshes
    a ``broker_page`` source directly — kept as its own matrix row by user
    decision 2026-10-09 so the operator can tell AI-pages and broker-pages
    apart in Actions logs. Shares the same ``settings.load_source_urls``
    list so the operator only maintains one place in the UI; the only
    difference from ``ai_page`` is the ``source`` tag written to persisted
    load rows.
    """

    kind: str = "broker_page"

    async def fetch(self, settings):  # type: ignore[override]
        raws = await super().fetch(settings)
        for r in raws:
            r.source = "broker_page"
        return raws


__all__ = ["AiPageSource", "BrokerPageSource"]
