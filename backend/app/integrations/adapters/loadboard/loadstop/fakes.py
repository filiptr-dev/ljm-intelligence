"""Fakes used by tests + ingest unit tests.

Shaped from the discovery walk — field names/casing from LoadStop's own
SPA requests. Values are **invented** (no real customer data, no real
MC/DOT numbers used in production LJM lanes).
"""

from __future__ import annotations

from typing import Any


class FakeAbpClient:
    """Drop-in for ``AbpClient`` — returns canned ABP ``items`` pages.

    Enforces the write-guard (so a test that forgets gets the same
    LoadstopWriteBlocked a prod call would).
    """

    def __init__(self, pages: dict[tuple[str, str], list[list[dict[str, Any]]]]):
        self._pages = pages
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def assert_read_only(method: str) -> None:
        from .http import AbpClient

        AbpClient.assert_read_only(method)

    async def call(self, service, method, body=None, *, query=None):
        self.assert_read_only(method)
        self.calls.append((service, method))
        pages = self._pages.get((service, method)) or [[]]
        return {"result": {"items": pages[0]}}

    async def paged(self, service, method, *, page_size=200, extra=None):
        self.assert_read_only(method)
        for p in self._pages.get((service, method)) or [[]]:
            self.calls.append((service, method))
            yield p


SAMPLE_TRUCKS = [
    {
        "id": "ls-tr-100",
        "unitNumber": "T-100",
        "make": "Freightliner",
        "model": "Cascadia",
        "vin": "1FUJGLDR0CSBE0001",
        "plate": "LJM-100",
        "status": "Available",
        "odometer": 412_355,
        "assignedDriverId": "ls-dr-10",
        "assignedDriverName": "Igor P.",
        "lastLocation": {
            "lat": 41.878,
            "lng": -87.629,
            "city": "Chicago",
            "state": "IL",
            "movedAt": "2026-10-10T22:14:00Z",
        },
    },
    {
        "id": "ls-tr-101",
        "unitNumber": "T-101",
        "make": "Volvo",
        "model": "VNL",
        "vin": "4V4NC9EJ4LN000001",
        "plate": "LJM-101",
        "status": "Moving",
        "odometer": 288_900,
        "assignedDriverId": "ls-dr-11",
        "assignedDriverName": "Marko S.",
        "lastLocation": {
            "lat": 39.962,
            "lng": -83.001,
            "city": "Columbus",
            "state": "OH",
            "movedAt": "2026-10-11T06:02:00Z",
        },
    },
]
