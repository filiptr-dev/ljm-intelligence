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

    async def paged(self, service, method, *, page_size=200, extra=None, result_path=None):
        self.assert_read_only(method)
        for p in self._pages.get((service, method)) or [[]]:
            self.calls.append((service, method))
            yield p


SAMPLE_TRUCKS = [
    # The grid envelope: identifying fields are nested under ``truck``;
    # the live-location string + assignedDriverName are on the outer row.
    {
        "truck": {
            "id": "ls-tr-100",
            "number": "T-100",
            "make": "Freightliner",
            "model": "Cascadia",
            "vin": "1FUJGLDR0CSBE0001",
            "licenseNumber": "LJM-100",
            "status": True,
            "outOfOrder": False,
            "currentMileage": 412_355,
        },
        "assignedDriverName": "Igor P.",
        "locationDescription": "Chicago, IL (moving)",
    },
    {
        "truck": {
            "id": "ls-tr-101",
            "number": "T-101",
            "make": "Volvo",
            "model": "VNL",
            "vin": "4V4NC9EJ4LN000001",
            "licenseNumber": "LJM-101",
            "status": True,
            "outOfOrder": False,
            "currentMileage": 288_900,
        },
        "assignedDriverName": "Marko S.",
        "locationDescription": "Columbus, OH (moving)",
    },
]
