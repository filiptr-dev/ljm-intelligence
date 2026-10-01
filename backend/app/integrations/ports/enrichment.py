"""Enrichment port — company lookup → company facts (FMCSA, websites, …)."""
from __future__ import annotations
from typing import Any, Protocol


class EnrichmentPort(Protocol):
    async def enrich(self, company: Any) -> Any: ...
