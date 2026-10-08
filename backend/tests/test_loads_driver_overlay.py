"""Driver overlay + settings PUT (plan 2026-10-08, dispatch 0027).

Env wins when set to something other than ``off``; otherwise the DB overlay
primed from the settings row wins; otherwise the default (``off``).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.integrations.adapters.loadboard.registry import (
    _DB_OVERLAY,
    _DriverSwitch,
    prime_overlay_from_db,
    set_db_overlay,
)
# Ensure every ORM model is registered on Base.metadata before create_all —
# otherwise a FK pointing at `email_templates` is unresolvable.
import app.models  # noqa: F401
from app.identity.models import SettingsRow


pytestmark = pytest.mark.asyncio


class _StubApi:
    enabled = False

    def reason(self) -> str:
        return "stub"

    async def fetch(self, _settings) -> list:  # pragma: no cover - unused
        return []

    async def test_connection(self, _settings):  # pragma: no cover
        from app.integrations.adapters.loadboard.base import ConnectionTest

        return ConnectionTest(ok=False, reason="stub")


class _Settings:
    loads_dat_driver = "off"
    loads_chr_driver = "off"
    loads_lb123_driver = "off"
    loads_truckstop_driver = "off"
    loads_agent_kill = ""


@pytest.fixture(autouse=True)
def _reset_overlay():
    _DB_OVERLAY.clear()
    yield
    _DB_OVERLAY.clear()


async def test_env_wins_over_overlay():
    """A non-off env value shadows the DB overlay."""
    s = _Settings()
    s.loads_dat_driver = "api"
    set_db_overlay(dat="agent")
    sw = _DriverSwitch("dat", _StubApi(), s)
    assert sw._mode() == "api"


async def test_overlay_wins_when_env_is_off():
    """env=off → fall back to DB overlay."""
    s = _Settings()
    set_db_overlay(dat="agent")
    sw = _DriverSwitch("dat", _StubApi(), s)
    assert sw._mode() == "agent"


async def test_default_off_when_nothing_set():
    sw = _DriverSwitch("dat", _StubApi(), _Settings())
    assert sw._mode() == "off"
    assert sw.enabled is False


async def test_prime_overlay_from_db_absent_safe():
    """A sessionmaker whose row doesn't exist leaves the overlay empty."""
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(e, expire_on_commit=False)
    await prime_overlay_from_db(sm)
    # No settings row seeded → overlay stays empty, _DriverSwitch returns off.
    sw = _DriverSwitch("dat", _StubApi(), _Settings())
    assert sw._mode() == "off"
    await e.dispose()


async def test_prime_overlay_reads_persisted_values():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(e, expire_on_commit=False)
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                loads_dat_driver="agent",
                loads_chr_driver="api",
                loads_agent_kill="on",
            )
        )
        await s.commit()
    await prime_overlay_from_db(sm)
    assert _DB_OVERLAY["dat"] == "agent"
    assert _DB_OVERLAY["chr"] == "api"
    assert _DB_OVERLAY["kill"] == "on"
    # Env kill empty but overlay says 'on' → _kill() True.
    sw = _DriverSwitch("dat", _StubApi(), _Settings())
    assert sw._kill() is True
    await e.dispose()


async def test_env_kill_overrides_overlay_off():
    """Env kill=1 kills even when overlay is off."""
    s = _Settings()
    s.loads_agent_kill = "1"
    set_db_overlay(kill="off")
    sw = _DriverSwitch("dat", _StubApi(), s)
    assert sw._kill() is True
