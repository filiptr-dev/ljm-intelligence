"""Load-board port — the common surface over DAT/CHR/123LB/Truckstop."""
from __future__ import annotations

from typing import Any, Protocol


class LoadBoardPort(Protocol):
    name: str
    async def search(self, q: Any) -> list[Any]: ...
