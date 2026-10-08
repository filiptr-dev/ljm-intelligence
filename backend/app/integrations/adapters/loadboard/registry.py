"""Load-source registry — one list, env-gated.

Each gated vendor (`dat` / `chr` / `loadboard123` / `truckstop`) is wrapped
in a small `_DriverSwitch` that reads `settings.loads_<src>_driver` and
picks the API client, the headless agent, or stays off. The vocabulary is
`off | api | agent` with `off` as default — adding the env never flips a
live source on (plan rule).
"""

from __future__ import annotations

from app.integrations.adapters.loadboard.agent_browser import AgentSource
from app.integrations.adapters.loadboard.ai_page import AiPageSource
from app.integrations.adapters.loadboard.base import ConnectionTest, LoadSource, RawLoad
from app.integrations.adapters.loadboard.chr import ChrSource
from app.integrations.adapters.loadboard.dat import DatSource
from app.integrations.adapters.loadboard.loadboard123 import LoadBoard123Source
from app.integrations.adapters.loadboard.paste import PasteSource
from app.integrations.adapters.loadboard.truckstop import TruckstopSource

_DRIVER_ATTR = {
    "dat": "loads_dat_driver",
    "chr": "loads_chr_driver",
    "loadboard123": "loads_lb123_driver",
    "truckstop": "loads_truckstop_driver",
}


class _DriverSwitch:
    """Composite that reads the per-source driver env and delegates."""

    def __init__(self, kind: str, api_source: LoadSource, settings) -> None:
        self.kind = kind
        self._api = api_source
        self._agent = AgentSource(src=kind)
        self._settings = settings

    def _mode(self) -> str:
        attr = _DRIVER_ATTR.get(self.kind, None)
        if attr is None:
            return "off"
        return str(getattr(self._settings, attr, "off") or "off")

    def _kill(self) -> bool:
        return (getattr(self._settings, "loads_agent_kill", "") or "") == "1"

    @property
    def enabled(self) -> bool:
        mode = self._mode()
        if mode == "off":
            return False
        if mode == "agent":
            return not self._kill()
        return bool(getattr(self._api, "enabled", False))

    def reason(self) -> str | None:
        mode = self._mode()
        if mode == "off":
            return "driver=off"
        if mode == "agent" and self._kill():
            return "agent_killed"
        if mode == "agent":
            return None
        api_reason = getattr(self._api, "reason", None)
        return api_reason() if callable(api_reason) else None

    async def fetch(self, settings) -> list[RawLoad]:
        mode = self._mode()
        if mode == "api":
            return await self._api.fetch(settings)
        if mode == "agent" and not self._kill():
            return await self._agent.fetch(settings)
        return []

    async def test_connection(self, settings) -> ConnectionTest:
        mode = self._mode()
        if mode == "api":
            return await self._api.test_connection(settings)
        if mode == "agent" and not self._kill():
            return await self._agent.test_connection(settings)
        return ConnectionTest(ok=False, reason=self.reason() or "driver=off")


def all_sources(settings) -> list[LoadSource]:
    return [
        AiPageSource(settings),
        PasteSource(),
        _DriverSwitch("dat", DatSource(settings), settings),
        _DriverSwitch("chr", ChrSource(settings), settings),
        _DriverSwitch("loadboard123", LoadBoard123Source(settings), settings),
        _DriverSwitch("truckstop", TruckstopSource(settings), settings),
    ]


def enabled_sources(settings) -> list[LoadSource]:
    return [s for s in all_sources(settings) if s.enabled]


def by_kind(settings, kind: str) -> LoadSource | None:
    for s in all_sources(settings):
        if s.kind == kind:
            return s
    return None


__all__ = ["all_sources", "by_kind", "enabled_sources"]
