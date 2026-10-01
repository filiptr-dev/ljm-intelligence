"""Load-source registry — one list, env-gated."""

from __future__ import annotations

from app.integrations.adapters.loadboard.ai_page import AiPageSource
from app.integrations.adapters.loadboard.base import LoadSource
from app.integrations.adapters.loadboard.chr import ChrSource
from app.integrations.adapters.loadboard.dat import DatSource
from app.integrations.adapters.loadboard.loadboard123 import LoadBoard123Source
from app.integrations.adapters.loadboard.paste import PasteSource
from app.integrations.adapters.loadboard.truckstop import TruckstopSource


def all_sources(settings) -> list[LoadSource]:
    return [
        AiPageSource(),
        PasteSource(),
        DatSource(settings),
        ChrSource(settings),
        LoadBoard123Source(settings),
        TruckstopSource(settings),
    ]


def enabled_sources(settings) -> list[LoadSource]:
    return [s for s in all_sources(settings) if s.enabled]


def by_kind(settings, kind: str) -> LoadSource | None:
    for s in all_sources(settings):
        if s.kind == kind:
            return s
    return None


__all__ = ["all_sources", "by_kind", "enabled_sources"]
