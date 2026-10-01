"""Paste source — holds operator-pasted lanes.

v1 just reports enabled + empty; the owner UI drops rows directly into
``loads`` via a route added by the live-loads-board slice.
"""

from __future__ import annotations

from app.sources.loads.base import ConnectionTest, RawLoad


class PasteSource:
    kind: str = "paste"
    enabled: bool = True

    async def fetch(self, settings) -> list[RawLoad]:
        return []

    async def test_connection(self, settings) -> ConnectionTest:
        return ConnectionTest(ok=True, latency_ms=0, reason="placeholder", sample_count=0)
