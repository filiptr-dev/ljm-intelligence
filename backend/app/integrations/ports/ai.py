"""AI provider port — Gemini / Claude / Null all speak this."""
from __future__ import annotations

from typing import Any, Protocol


class AIProviderPort(Protocol):
    async def complete(self, feature: str, prompt: Any) -> Any: ...
