"""Env > DB overlay for agent-browser URL + token.

The resolver is the single source of truth: env (``AGENT_BROWSER_URL`` /
``AGENT_BROWSER_TOKEN``) wins when set; otherwise the DB settings row + vault.
"""

from __future__ import annotations

import types

import pytest

from app.integrations.adapters.loadboard.agent_browser import config as abc


class _NoRow:
    """Sessionmaker that returns no SettingsRow and no vault."""

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, *_a, **_kw):
            class _R:
                def scalar_one_or_none(self_inner):
                    return None

            return _R()

    def __call__(self):
        return self._Session()


class _StubRow:
    def __init__(self, url: str) -> None:
        self.agent_browser_url = url


class _WithRow:
    def __init__(self, url: str) -> None:
        self._url = url

    class _Session:
        def __init__(self, url: str) -> None:
            self._url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, *_a, **_kw):
            url = self._url

            class _R:
                def scalar_one_or_none(self_inner):
                    return _StubRow(url)

            return _R()

    def __call__(self):
        return self._Session(self._url)


@pytest.mark.asyncio
async def test_env_url_and_token_win(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = types.SimpleNamespace(
        agent_browser_url="https://env.example", agent_browser_token="env-tok"
    )
    cfg = await abc.resolve(settings, _NoRow())
    assert cfg.url == "https://env.example"
    assert cfg.token == "env-tok"
    assert cfg.ok is True


@pytest.mark.asyncio
async def test_db_url_fills_when_env_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = types.SimpleNamespace(agent_browser_url="", agent_browser_token="")
    # Vault calls will raise VaultConfigError in this env — the resolver swallows.
    cfg = await abc.resolve(settings, _WithRow("https://db.example"))
    assert cfg.url == "https://db.example"
    # Token stays empty — no vault configured in the stub session.
    assert cfg.token == ""
    assert cfg.ok is True


@pytest.mark.asyncio
async def test_unset_everywhere_is_not_ok() -> None:
    settings = types.SimpleNamespace(agent_browser_url="", agent_browser_token="")
    cfg = await abc.resolve(settings, _NoRow())
    assert cfg.url == ""
    assert cfg.token == ""
    assert cfg.ok is False


@pytest.mark.asyncio
async def test_env_wins_over_db_url() -> None:
    settings = types.SimpleNamespace(
        agent_browser_url="https://env.example", agent_browser_token=""
    )
    cfg = await abc.resolve(settings, _WithRow("https://db.example"))
    assert cfg.url == "https://env.example"
