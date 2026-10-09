"""Fleet-source registry — one list, one active source.

Today that is the demo fleet. When the carrier's real fleet software is wired,
its adapter joins ``all_sources`` and ``active_source`` picks it (credentials
come from the DB vault, never env). Nothing else in the app changes.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet.adapters.demo import DemoFleetSource
from app.fleet.ports import FleetSource


def all_sources(session: AsyncSession) -> list[FleetSource]:
    return [DemoFleetSource(session)]


def active_source(session: AsyncSession) -> FleetSource:
    return next(s for s in all_sources(session) if s.enabled)


__all__ = ["active_source", "all_sources"]
