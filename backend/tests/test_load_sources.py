"""Load-source adapters — enabled triangulation + row mapping.

For each vendor adapter: (a) env absent → ``enabled=False``, ``fetch`` returns
``[]``; (b) env present → ``enabled=True``. Live HTTP paths are not exercised
here; the mapping functions are checked with a synthetic row so a vendor
field-name change surfaces as a single-file edit.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import ChrSettings, DatSettings, Lb123Settings, Settings, TruckstopSettings
from app.integrations.adapters.loadboard.chr import ChrSource, map_chr_row
from app.integrations.adapters.loadboard.dat import DatSource, map_dat_row
from app.integrations.adapters.loadboard.loadboard123 import LoadBoard123Source, map_lb123_row
from app.integrations.adapters.loadboard.registry import all_sources, by_kind, enabled_sources
from app.integrations.adapters.loadboard.truckstop import TruckstopSource, map_truckstop_row


# Legacy flat kwargs → grouped sub-model, so existing call sites keep working
# after the settings-grouping refactor without rewriting every assertion.
_FLAT_TO_GROUP: dict[str, tuple[str, str]] = {
    "dat_service_account_email": ("dat", "service_account_email"),
    "dat_service_account_password": ("dat", "service_account_password"),
    "dat_org_id": ("dat", "org_id"),
    "chr_client_id": ("chr", "client_id"),
    "chr_client_secret": ("chr", "client_secret"),
    "chr_carrier_code": ("chr", "carrier_code"),
    "lb123_api_key": ("lb123", "api_key"),
    "lb123_carrier_username": ("lb123", "carrier_username"),
    "lb123_carrier_password": ("lb123", "carrier_password"),
    "truckstop_integration_id": ("truckstop", "integration_id"),
    "truckstop_username": ("truckstop", "username"),
    "truckstop_password": ("truckstop", "password"),
}
_GROUP_CLASSES = {
    "dat": DatSettings,
    "chr": ChrSettings,
    "lb123": Lb123Settings,
    "truckstop": TruckstopSettings,
}


def _s(**overrides) -> Settings:
    groups: dict[str, dict] = {}
    native: dict = {}
    for k, v in overrides.items():
        if k in _FLAT_TO_GROUP:
            grp, field = _FLAT_TO_GROUP[k]
            groups.setdefault(grp, {})[field] = v
        else:
            native[k] = v
    for grp, kwargs in groups.items():
        native[grp] = _GROUP_CLASSES[grp](**kwargs)
    return Settings().model_copy(update=native)


@pytest.mark.asyncio
async def test_dat_disabled_without_env() -> None:
    s = DatSource(_s())
    assert s.enabled is False
    assert "DAT_SERVICE_ACCOUNT_EMAIL" in (s.reason() or "")
    assert await s.fetch(_s()) == []


@pytest.mark.asyncio
async def test_dat_enabled_with_env() -> None:
    s = DatSource(
        _s(
            dat_service_account_email="sa@dat",
            dat_service_account_password=SecretStr("pw"),
            dat_org_id="ORG",
        )
    )
    assert s.enabled is True
    assert s.reason() is None


def test_dat_row_mapping() -> None:
    row = {
        "matchId": "DM-1",
        "postersCompany": {"name": "Acme Brokerage"},
        "contact": {"email": "x@acme", "phone": "555"},
        "origin": {"city": "Dallas", "stateProv": "TX"},
        "destination": {"city": "Atlanta", "stateProv": "GA"},
        "availability": {"earliestWhen": "2026-10-02T00:00:00Z"},
        "equipmentType": "V",
        "rateInfo": {"rateUsd": 1500.0},
        "tripDistance": {"miles": 780},
        "postedWhen": "2026-10-01T12:00:00Z",
    }
    rl = map_dat_row(row)
    assert rl.source == "dat" and rl.source_ref == "DM-1"
    assert rl.broker_name == "Acme Brokerage"
    assert rl.origin_state == "TX" and rl.dest_state == "GA"
    assert rl.rate_usd == 1500.0
    assert rl.miles == 780


@pytest.mark.asyncio
async def test_chr_env_gate_and_mapping() -> None:
    assert ChrSource(_s()).enabled is False
    s = ChrSource(_s(chr_client_id="cid", chr_client_secret=SecretStr("sec"), chr_carrier_code="CARRIER"))
    assert s.enabled is True
    rl = map_chr_row(
        {
            "load_id": "L1",
            "origin": {"city": "Chicago", "state": "IL"},
            "destination": {"city": "Denver", "state": "CO"},
        }
    )
    assert rl.broker_name == "C.H. Robinson" and rl.source_ref == "L1"
    assert rl.origin_state == "IL" and rl.dest_state == "CO"


@pytest.mark.asyncio
async def test_lb123_env_gate_and_mapping() -> None:
    assert LoadBoard123Source(_s()).enabled is False
    s = LoadBoard123Source(
        _s(
            lb123_api_key=SecretStr("K"),
            lb123_carrier_username="u",
            lb123_carrier_password=SecretStr("p"),
        )
    )
    assert s.enabled is True
    rl = map_lb123_row({"load_id": "LB1", "broker": {"company_name": "BrokerCo"}})
    assert rl.broker_name == "BrokerCo" and rl.source == "loadboard123"


@pytest.mark.asyncio
async def test_truckstop_env_gate_and_mapping() -> None:
    assert TruckstopSource(_s()).enabled is False
    s = TruckstopSource(
        _s(
            truckstop_integration_id=SecretStr("I"),
            truckstop_username="u",
            truckstop_password=SecretStr("p"),
        )
    )
    assert s.enabled is True
    rl = map_truckstop_row({"loadId": "T1", "postersCompanyName": "Posters LLC"})
    assert rl.broker_name == "Posters LLC" and rl.source == "truckstop"


def test_registry_counts_six() -> None:
    assert [s.kind for s in all_sources(_s())] == [
        "ai_page",
        "paste",
        "dat",
        "chr",
        "loadboard123",
        "truckstop",
    ]
    # By default only ``paste`` is enabled: ``ai_page`` needs the agent sidecar
    # URL + at least one enabled source URL, and the vendor sources are behind
    # the ``loads_<src>_driver`` switch which defaults to ``off``.
    kinds = [s.kind for s in enabled_sources(_s())]
    assert set(kinds) == {"paste"}


def test_registry_by_kind() -> None:
    assert by_kind(_s(), "truckstop") is not None
    assert by_kind(_s(), "no-such-source") is None
