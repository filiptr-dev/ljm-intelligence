"""Shared seeding for the lanes tests: the migration-0037 generator (0036 on request), into sqlite."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from app.analysis.models import FreightRun
from app.rates.domain import CityState, haversine_miles, highway_miles, resolve_city

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
TENANT = "01LJMORGLJM00000000000000A"

_VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
_FILES = {"0036": "0036_demo_freight_runs.py", "0037": "0037_reseed_demo_freight_runs.py"}


def load_migration(rev: str = "0037"):
    spec = importlib.util.spec_from_file_location(f"mig{rev}", _VERSIONS / _FILES[rev])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve(city: str, state: str) -> tuple[float, float]:
    p = resolve_city(CityState(city, state))
    return p.lat, p.lon


def demo_rows(now: datetime = NOW, rev: str = "0037") -> list[dict]:
    mod = load_migration(rev)
    return mod.generate_runs(
        now, lambda s, d: None, _resolve,
        lambda a, b, c, d: highway_miles(haversine_miles(a, b, c, d)),
    )


async def seed(sm, now: datetime = NOW, tenant: str = TENANT) -> int:
    rows = demo_rows(now)
    async with sm() as s:
        s.add_all([FreightRun(**{**r, "tenant_id": tenant}) for r in rows])
        await s.commit()
    return len(rows)
