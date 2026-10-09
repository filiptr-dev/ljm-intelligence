"""Load-adapter live-fetch triangulation — ok / API-down / env-absent per vendor.

All HTTP goes through ``httpx.MockTransport`` monkey-patched into each
adapter's module-level ``_client_factory`` symbol. No real vendor calls.
"""

from __future__ import annotations

import httpx
from pydantic import SecretStr

from app.config import ChrSettings, DatSettings, Lb123Settings, Settings, TruckstopSettings
from app.integrations.adapters.loadboard import (
    chr as chr_mod,
    dat as dat_mod,
    loadboard123 as lb_mod,
    truckstop as ts_mod,
)


def _s(**grp_overrides) -> Settings:
    native: dict = {}
    for key, val in grp_overrides.items():
        native[key] = val
    return Settings().model_copy(update=native)


def _patch_client(monkeypatch, module, handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module, "_client_factory", factory)


# --------------------------------------------------------------------- DAT ---


def _dat_settings() -> Settings:
    return _s(
        dat=DatSettings(
            service_account_email="sa@dat",
            service_account_password=SecretStr("pw"),
            org_id="ORG",
        )
    )


async def test_dat_fetch_happy_path(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/auth/v2/token/organization":
            return httpx.Response(200, json={"accessToken": "org-tok"})
        if request.url.path == "/auth/v2/token/user":
            return httpx.Response(200, json={"accessToken": "user-tok"})
        if request.url.path == "/search/v3/loads":
            return httpx.Response(
                200,
                json={
                    "matches": [
                        {
                            "matchId": "DM-1",
                            "postersCompany": {"name": "Acme"},
                            "origin": {"city": "Dallas", "stateProv": "TX"},
                            "destination": {"city": "Atlanta", "stateProv": "GA"},
                            "equipmentType": "V",
                            "rateInfo": {"rateUsd": 1500.0},
                            "tripDistance": {"miles": 780},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    _patch_client(monkeypatch, dat_mod, handler)
    src = dat_mod.DatSource(_dat_settings())
    rows = await src.fetch(_dat_settings())
    assert len(rows) == 1 and rows[0].source_ref == "DM-1"
    assert "/auth/v2/token/organization" in calls
    assert "/auth/v2/token/user" in calls
    assert "/search/v3/loads" in calls


async def test_dat_fetch_api_down_returns_empty(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    _patch_client(monkeypatch, dat_mod, handler)
    src = dat_mod.DatSource(_dat_settings())
    rows = await src.fetch(_dat_settings())
    assert rows == []


async def test_dat_fetch_env_absent_returns_empty(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("fetch must not hit HTTP when env is absent")

    _patch_client(monkeypatch, dat_mod, handler)
    src = dat_mod.DatSource(_s())
    assert src.enabled is False
    assert await src.fetch(_s()) == []


# --------------------------------------------------------------------- CHR ---


def _chr_settings() -> Settings:
    return _s(
        chr=ChrSettings(
            client_id="cid",
            client_secret=SecretStr("sec"),
            carrier_code="CARRIER",
        )
    )


async def test_chr_fetch_happy_path(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/v2.0/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})
        if request.url.path == "/carrier/v1/loads/available":
            return httpx.Response(
                200,
                json={
                    "loads": [
                        {
                            "load_id": "L1",
                            "origin": {"city": "Chicago", "state": "IL"},
                            "destination": {"city": "Denver", "state": "CO"},
                            "equipment": "V",
                            "rate_usd": 2000,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    _patch_client(monkeypatch, chr_mod, handler)
    src = chr_mod.ChrSource(_chr_settings())
    rows = await src.fetch(_chr_settings())
    assert len(rows) == 1 and rows[0].broker_name == "C.H. Robinson"


async def test_chr_fetch_api_down(monkeypatch) -> None:
    _patch_client(monkeypatch, chr_mod, lambda r: httpx.Response(502, text="x"))
    rows = await chr_mod.ChrSource(_chr_settings()).fetch(_chr_settings())
    assert rows == []


async def test_chr_fetch_env_absent() -> None:
    assert await chr_mod.ChrSource(_s()).fetch(_s()) == []


# ------------------------------------------------------------------- LB123 ---


def _lb_settings() -> Settings:
    return _s(
        lb123=Lb123Settings(
            api_key=SecretStr("K"),
            carrier_username="u",
            carrier_password=SecretStr("p"),
        )
    )


async def test_lb123_fetch_happy_path(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"session_token": "sess", "expires_in": 1800})
        if request.url.path == "/loads/search":
            return httpx.Response(
                200,
                json={"loads": [{"load_id": "LB1", "broker": {"company_name": "BrokerCo"}}]},
            )
        return httpx.Response(404)

    _patch_client(monkeypatch, lb_mod, handler)
    rows = await lb_mod.LoadBoard123Source(_lb_settings()).fetch(_lb_settings())
    assert len(rows) == 1 and rows[0].source_ref == "LB1"


async def test_lb123_fetch_api_down(monkeypatch) -> None:
    _patch_client(monkeypatch, lb_mod, lambda r: httpx.Response(500))
    rows = await lb_mod.LoadBoard123Source(_lb_settings()).fetch(_lb_settings())
    assert rows == []


async def test_lb123_fetch_env_absent() -> None:
    assert await lb_mod.LoadBoard123Source(_s()).fetch(_s()) == []


# ---------------------------------------------------------------- Truckstop ---


def _ts_settings() -> Settings:
    return _s(
        truckstop=TruckstopSettings(
            integration_id=SecretStr("I"),
            username="u",
            password=SecretStr("p"),
        )
    )


async def test_truckstop_fetch_happy_path(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("IntegrationId") == "I"
        return httpx.Response(
            200,
            json={"loads": [{"loadId": "T1", "postersCompanyName": "Posters LLC"}]},
        )

    _patch_client(monkeypatch, ts_mod, handler)
    rows = await ts_mod.TruckstopSource(_ts_settings()).fetch(_ts_settings())
    assert len(rows) == 1 and rows[0].source_ref == "T1"


async def test_truckstop_fetch_api_down(monkeypatch) -> None:
    _patch_client(monkeypatch, ts_mod, lambda r: httpx.Response(503))
    rows = await ts_mod.TruckstopSource(_ts_settings()).fetch(_ts_settings())
    assert rows == []


async def test_truckstop_fetch_env_absent() -> None:
    assert await ts_mod.TruckstopSource(_s()).fetch(_s()) == []


# ------------------------------------------- refresh_all idempotency (unit) ---


class _FakeSession:
    """Minimal AsyncSession shim — one IntegrityError on first-dup, otherwise ok."""

    def __init__(self, seen: set[str]) -> None:
        self._seen = seen
        self._row: object | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def add(self, row) -> None:
        from sqlalchemy.exc import IntegrityError

        key = f"{row.source}:{row.source_ref}"
        if key in self._seen:
            raise IntegrityError("dup", {}, Exception("dup"))
        self._row = (row.source, row.source_ref)

    async def commit(self) -> None:
        if self._row:
            self._seen.add(f"{self._row[0]}:{self._row[1]}")

    async def rollback(self) -> None:
        self._row = None


def _fake_sessionmaker():
    seen: set[str] = set()

    def mk():
        return _FakeSession(seen)

    mk.seen = seen  # type: ignore[attr-defined]
    return mk


async def test_refresh_source_is_idempotent_on_source_ref(monkeypatch) -> None:
    """Running loads.refresh twice on the same vendor rows UPSERTs: no dup inserts."""
    from app.integrations import loads_service

    # Stub DAT to return one deterministic row.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v2/token/organization":
            return httpx.Response(200, json={"accessToken": "o"})
        if request.url.path == "/auth/v2/token/user":
            return httpx.Response(200, json={"accessToken": "u"})
        return httpx.Response(
            200,
            json={"matches": [{"matchId": "DM-DUP", "postersCompany": {"name": "A"}}]},
        )

    _patch_client(monkeypatch, dat_mod, handler)
    sm = _fake_sessionmaker()

    # Vendor sources sit behind a driver switch that defaults to "off";
    # opt DAT into the API driver so refresh actually fetches.
    settings = _dat_settings().model_copy(update={"loads_dat_driver": "api"})
    stats_a = await loads_service.refresh_source(sm, settings, "dat")
    stats_b = await loads_service.refresh_source(sm, settings, "dat")
    assert stats_a.inserted == 1 and stats_a.skipped == 0
    assert stats_b.inserted == 0 and stats_b.skipped == 1  # idempotent
