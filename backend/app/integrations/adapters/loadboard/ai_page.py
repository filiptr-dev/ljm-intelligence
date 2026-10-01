"""AI-pages load source (public broker boards extractor stub).

v1 is a placeholder — always enabled, always returns []. The real extractor
(gemini on a public broker board page) lands with the live-loads-board slice.
"""

from __future__ import annotations

from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad


class AiPageSource:
    kind: str = "ai_page"
    enabled: bool = True

    async def fetch(self, settings) -> list[RawLoad]:
        return []

    async def test_connection(self, settings) -> ConnectionTest:
        return ConnectionTest(ok=True, latency_ms=0, reason="placeholder", sample_count=0)
