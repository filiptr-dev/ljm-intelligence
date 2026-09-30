"""FMCSA source — offline tests (Slice 1: in-query region filter + keyset paginator).

All requests go through ``httpx.MockTransport`` — no network. Mirrors the pattern
used by ``tests/test_osm_overpass.py``.
"""

from __future__ import annotations

import httpx

from app.region import IN_REGION_STATES
from app.sources import fmcsa as fmcsa_mod
from app.sources.fmcsa import SODA_URL, fetch_fmcsa


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _row(*, dot: str, add_date: str, state: str = "NY", carship: str = "B", legal_name: str = "Acme") -> dict:
    return {
        "dot_number": dot,
        "docket1prefix": "MC",
        "docket1": dot,  # reuse dot as MC # for simplicity
        "legal_name": legal_name,
        "carship": carship,
        "phy_state": state,
        "phy_city": "NYC",
        "phy_street": "1 Main",
        "phone": "212-555-0100",
        "email_address": None,
        "status_code": "A",
        "add_date": add_date,
    }


# --- AC1 ----------------------------------------------------------------------

async def test_fmcsa_where_contains_all_region_states():
    """AC1: the outgoing ``$where`` names ``phy_state IN (...)`` with every
    in-region state from ``region.IN_REGION_STATES``.
    """
    captured: dict[str, httpx.Request] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["req"] = req
        return httpx.Response(200, json=[])

    async with _mock_client(handler) as client:
        async for _page in fetch_fmcsa(page_size=500, max_pages=1, client=client):
            pass

    req = captured["req"]
    assert req.url.path.endswith("/resource/az4n-8mr2.json")
    where = req.url.params["$where"]
    # Every in-region state code must appear inside a `phy_state IN (...)` clause.
    assert "phy_state IN (" in where
    for state in IN_REGION_STATES:
        assert f"'{state}'" in where, f"missing state {state} in $where: {where}"
    # Existing filters preserved.
    assert "status_code='A'" in where
    assert "carship LIKE '%B%'" in where
    assert "carship LIKE '%S%'" in where
    assert "carship LIKE '%F%'" in where
    # Order is stable: add_date DESC, dot_number DESC.
    assert req.url.params["$order"] == "add_date DESC, dot_number DESC"


# --- Paginator ----------------------------------------------------------------

async def test_paginator_keyset_advances_on_page_2():
    """Page 2's ``$where`` includes the keyset clause built from page 1's last row."""
    # SODA ``add_date`` is 8-char YYYYMMDD text — lexical order == chronological.
    pages = [
        # page 1: full page (page_size=2) — will trigger a page 2 fetch
        [
            _row(dot="1000", add_date="20260930"),
            _row(dot="900", add_date="20260929"),
        ],
        # page 2: short page — terminates the generator
        [_row(dot="800", add_date="20260928")],
    ]
    seen_where: list[str] = []
    call_idx = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        seen_where.append(req.url.params["$where"])
        payload = pages[call_idx["n"]]
        call_idx["n"] += 1
        return httpx.Response(200, json=payload)

    collected: list[list] = []
    async with _mock_client(handler) as client:
        async for page in fetch_fmcsa(page_size=2, max_pages=5, client=client):
            collected.append(page)

    # Two HTTP calls were made — page 3 is never requested because page 2 was short.
    assert call_idx["n"] == 2
    assert len(collected) == 2

    # Page 1: no keyset clause.
    assert "add_date <" not in seen_where[0]

    # Page 2: keyset built from page 1's last row (dot=900, add_date=20260929).
    where2 = seen_where[1]
    assert "add_date < '20260929'" in where2
    assert "add_date = '20260929'" in where2
    assert "dot_number < 900" in where2


async def test_paginator_respects_max_pages():
    """``max_pages`` is a hard cap even when pages remain full."""
    call_idx = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_idx["n"] += 1
        # Every response is a full page → generator would go forever without the cap.
        return httpx.Response(
            200,
            json=[
                _row(dot=str(2000 - call_idx["n"] * 10), add_date=f"202609{30 - call_idx['n']:02d}"),
                _row(dot=str(1999 - call_idx["n"] * 10), add_date=f"202609{30 - call_idx['n']:02d}"),
            ],
        )

    collected: list[list] = []
    async with _mock_client(handler) as client:
        async for page in fetch_fmcsa(page_size=2, max_pages=2, client=client):
            collected.append(page)

    assert len(collected) == 2
    assert call_idx["n"] == 2


async def test_paginator_stops_on_short_page_before_max_pages():
    """A short page (fewer rows than page_size) terminates even below max_pages."""
    call_idx = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_idx["n"] += 1
        # One-row response, page_size=10 → the paginator should not ask for page 2.
        return httpx.Response(
            200,
            json=[_row(dot="777", add_date="20260930")],
        )

    collected: list[list] = []
    async with _mock_client(handler) as client:
        async for page in fetch_fmcsa(page_size=10, max_pages=5, client=client):
            collected.append(page)

    assert call_idx["n"] == 1
    assert len(collected) == 1
    assert len(collected[0]) == 1


async def test_app_token_sent_as_header():
    """When an app token is provided it rides as ``X-App-Token``; absent otherwise."""
    seen_headers: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(req.headers))
        return httpx.Response(200, json=[])

    async with _mock_client(handler) as client:
        async for _p in fetch_fmcsa(page_size=1, max_pages=1, app_token="tok-123", client=client):
            pass
    assert seen_headers[-1].get("x-app-token") == "tok-123"

    async with _mock_client(handler) as client:
        async for _p in fetch_fmcsa(page_size=1, max_pages=1, client=client):
            pass
    assert "x-app-token" not in seen_headers[-1]


def test_soda_url_unchanged():
    """Guard against an accidental endpoint change during the paginator refactor."""
    assert SODA_URL == "https://data.transportation.gov/resource/az4n-8mr2.json"
    # Module keeps the row-to-lead helper importable for other tests.
    assert callable(fmcsa_mod._row_to_lead)
