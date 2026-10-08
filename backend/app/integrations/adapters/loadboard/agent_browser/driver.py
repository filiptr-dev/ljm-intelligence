"""``LoadSource``-shaped facade over the agent-browser sidecar.

One instance per real source. Reads env (``LOADS_<SRC>_DRIVER=agent``,
``LOADS_AGENT_KILL``, ``AGENT_BROWSER_URL``), decides ``enabled``, runs the
tool-loop, writes an ``AgentRun`` row on every attempt.

The *vault* access (session read/write, scripted login) happens through the
dedicated session helpers in :mod:`app.integrations.loads_service`; the
driver's job is strictly orchestration + accounting.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.integrations.adapters.ai import provider as ai_provider
from app.integrations.adapters.loadboard.agent_browser.agent import (
    AgentCaps,
    AgentResult,
    run as run_agent,
)
from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad

log = logging.getLogger(__name__)


_DRIVER_ENV_ATTR = {
    "dat": "loads_dat_driver",
    "chr": "loads_chr_driver",
    "loadboard123": "loads_lb123_driver",
    "truckstop": "loads_truckstop_driver",
    "ai_page": None,  # public broker boards: no API path, agent only
    "broker_page": None,
}


def _driver_for(settings, src: str) -> str:
    """Return ``off | api | agent`` for ``src``.

    Unknown source → ``off`` (defensive). The settings row isn't consulted
    here — env wins; the Settings UI write path (future) will set env-shaped
    values that this reads back.
    """
    attr = _DRIVER_ENV_ATTR.get(src, None)
    if attr is None:
        # broker_page / ai_page — always agent when agent sidecar is on.
        return "agent" if (getattr(settings, "agent_browser_url", "") or "") else "off"
    return str(getattr(settings, attr, "off") or "off")


class AgentSource:
    """``LoadSource`` adapter backed by the headless-agent sidecar."""

    def __init__(self, src: str, *, start_url: str | None = None,
                 allowlist: list[str] | None = None, sessionmaker=None) -> None:
        self.kind = src
        self._start_url = start_url or ""
        self._allowlist = allowlist or ([start_url] if start_url else [])
        self._sessionmaker = sessionmaker

    def _env_ok(self, settings) -> tuple[bool, str | None]:
        if (getattr(settings, "loads_agent_kill", "") or "") == "1":
            return False, "agent_killed"
        if not (getattr(settings, "agent_browser_url", "") or ""):
            return False, "agent_browser_url_unset"
        if _driver_for(settings, self.kind) != "agent" and self.kind not in {"ai_page", "broker_page"}:
            return False, "driver_not_agent"
        return True, None

    @property
    def enabled(self) -> bool:
        # Signature-compatible with the vendor adapters; the real check lives
        # in :meth:`fetch` since we need the live settings object for the env
        # reads. Here we optimistically say True — the fetch short-circuits
        # cleanly when the env is off.
        return True

    def reason(self) -> str | None:
        return None

    async def _count_runs_today(self, settings) -> int:
        if self._sessionmaker is None:
            return 0
        from app.prospecting.models import AgentRun

        async with self._sessionmaker() as s:
            today = datetime.now(UTC).date()
            row = await s.execute(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.source == self.kind)
                .where(func.date(AgentRun.started_at) == today)
            )
            return int(row.scalar() or 0)

    async def _log_run(self, settings, result: AgentResult) -> None:
        if self._sessionmaker is None:
            return
        from app.prospecting.models import AgentRun

        async with self._sessionmaker() as s:
            s.add(
                AgentRun(
                    source=self.kind,
                    ended_at=datetime.now(UTC),
                    status=result.status,
                    steps=result.steps,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    error=result.error,
                )
            )
            await s.commit()

    async def fetch(self, settings) -> list[RawLoad]:
        ok, reason = self._env_ok(settings)
        if not ok:
            log.info("agent/%s: disabled (%s)", self.kind, reason)
            return []
        cap = int(getattr(settings, "agent_daily_runs_per_source", 48) or 48)
        if await self._count_runs_today(settings) >= cap:
            await self._log_run(settings, AgentResult(status="cap_exceeded"))
            log.info("agent/%s: daily cap %d reached", self.kind, cap)
            return []
        provider = ai_provider.get_for(
            "loads_agent",
            settings=settings,
            ai_features=None,
        )
        caps = AgentCaps(
            max_steps=int(getattr(settings, "agent_max_steps", 20) or 20),
            max_input_tokens=int(getattr(settings, "agent_max_input_tokens", 120000) or 120000),
            max_output_tokens=int(getattr(settings, "agent_max_output_tokens", 4000) or 4000),
        )
        session_id = secrets.token_hex(8)
        start_url = self._start_url or f"about:source/{self.kind}"
        result = await run_agent(
            provider=provider,
            agent_browser_url=settings.agent_browser_url,
            source=self.kind,
            start_url=start_url,
            session_id=session_id,
            allowlist=self._allowlist or [start_url],
            caps=caps,
        )
        await self._log_run(settings, result)
        return result.loads

    async def test_connection(self, settings) -> ConnectionTest:
        ok, reason = self._env_ok(settings)
        return ConnectionTest(ok=ok, latency_ms=0, reason=reason, sample_count=0)


__all__ = ["AgentSource"]
