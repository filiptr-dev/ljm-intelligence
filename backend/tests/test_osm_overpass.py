"""Overpass source — no network. httpx.MockTransport returns canned JSON.

Covers:
  * Named `distribution_centre` → kept with real name.
  * Unnamed `building=warehouse` → kept, label synthesised from `addr:city`.
  * Unnamed `building=warehouse` with no `addr:city` → falls back to state code.
  * Element without the required tags → dropped (defence for cached payloads).
  * Fetch failure (HTTP 500 / connect error) → returns [], never raises.
  * Cache: a second call with cache hot skips the transport entirely.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.adapters.web import osm_overpass


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _payload(*elements: dict) -> dict:
    return {"version": 0.6, "generator": "mock", "elements": list(elements)}


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "overpass"


async def test_named_distribution_centre_kept(cache_dir: Path):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        payload = _payload(
            {
                "type": "way",
                "id": 42,
                "center": {"lat": 40.7, "lon": -74.2},
                "tags": {
                    "industrial": "distribution_centre",
                    "name": "Acme DC",
                    "addr:city": "Newark",
                },
            }
        )
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    assert calls["n"] == 1
    assert len(elements) == 1
    assert elements[0].ref == "way/42"
    incoming = osm_overpass.element_to_incoming(elements[0], "NJ")
    assert incoming is not None
    assert incoming.name == "Acme DC"
    assert incoming.osm_ref == "way/42"
    assert incoming.city == "Newark"
    assert incoming.state == "NJ"


async def test_unnamed_warehouse_synthesised_label(cache_dir: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                {
                    "type": "node",
                    "id": 7,
                    "lat": 40.1,
                    "lon": -74.5,
                    "tags": {"building": "warehouse", "addr:city": "Trenton"},
                }
            ),
        )

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    inc = osm_overpass.element_to_incoming(elements[0], "NJ")
    assert inc is not None
    assert inc.name == "Unnamed warehouse near Trenton"


async def test_unnamed_warehouse_no_city_uses_state(cache_dir: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                {
                    "type": "node",
                    "id": 8,
                    "lat": 40.1,
                    "lon": -74.5,
                    "tags": {"building": "warehouse"},
                }
            ),
        )

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    inc = osm_overpass.element_to_incoming(elements[0], "NJ")
    assert inc is not None
    assert inc.name == "Unnamed warehouse near NJ"


async def test_element_without_required_tag_dropped(cache_dir: Path):
    # A cached payload might contain e.g. landuse=industrial; we defensively drop.
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                {
                    "type": "way",
                    "id": 9,
                    "center": {"lat": 40.1, "lon": -74.5},
                    "tags": {"landuse": "industrial", "name": "Rail Yard"},
                }
            ),
        )

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    assert len(elements) == 1  # element itself still parses
    assert osm_overpass.element_to_incoming(elements[0], "NJ") is None


async def test_http_500_returns_empty_never_raises(cache_dir: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream down")

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    assert elements == []


async def test_connect_error_returns_empty(cache_dir: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    assert elements == []


async def test_cache_hit_skips_transport(cache_dir: Path):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json=_payload(
                {
                    "type": "way",
                    "id": 1,
                    "center": {"lat": 40.7, "lon": -74.2},
                    "tags": {"industrial": "distribution_centre", "name": "X"},
                }
            ),
        )

    async with _mock_client(handler) as client:
        first = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
        second = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    assert calls["n"] == 1  # second call served from cache
    assert len(first) == len(second) == 1


async def test_cache_ttl_expired_refetches(cache_dir: Path, monkeypatch):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json=_payload(
                {
                    "type": "way",
                    "id": 1,
                    "center": {"lat": 40.7, "lon": -74.2},
                    "tags": {"industrial": "distribution_centre", "name": "X"},
                }
            ),
        )

    async with _mock_client(handler) as client:
        await osm_overpass.fetch_overpass_elements(
            "NJ", client=client, cache_dir=cache_dir, throttle=False, cache_ttl_seconds=0
        )
        await osm_overpass.fetch_overpass_elements(
            "NJ", client=client, cache_dir=cache_dir, throttle=False, cache_ttl_seconds=0
        )
    assert calls["n"] == 2


async def test_unknown_state_bbox_returns_empty(cache_dir: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called for out-of-region state")

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("TX", client=client, cache_dir=cache_dir, throttle=False)
    assert elements == []


async def test_cache_written_to_disk(cache_dir: Path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                {
                    "type": "node",
                    "id": 5,
                    "lat": 40.7,
                    "lon": -74.2,
                    "tags": {"building": "warehouse", "name": "X"},
                }
            ),
        )

    async with _mock_client(handler) as client:
        await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    # The single cache file is a valid JSON with an `elements` list.
    files = list(cache_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert isinstance(payload.get("elements"), list)


async def test_raise_on_error_reraises_http_failure(cache_dir: Path):
    """crawl-depth plan slice 3: callers can opt into exceptions so they can
    count Overpass failures as `osm_states_failed` rather than silently seeing []."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="overpass down")

    async with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPError):
            await osm_overpass.fetch_overpass_elements(
                "NJ",
                client=client,
                cache_dir=cache_dir,
                throttle=False,
                raise_on_error=True,
            )


async def test_default_swallows_http_failure(cache_dir: Path):
    """Default behaviour unchanged — still returns [] on HTTP failure so existing
    call sites (that don't opt in) can't regress into a new exception path."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="overpass down")

    async with _mock_client(handler) as client:
        elements = await osm_overpass.fetch_overpass_elements("NJ", client=client, cache_dir=cache_dir, throttle=False)
    assert elements == []
