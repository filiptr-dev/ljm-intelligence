"""Shared `httpx.AsyncClient` — one per process, created in the lifespan.

Rationale: each adapter module (FMCSA, OSM, Gemini, Claude, Gmail, load boards)
used to build its own `httpx.AsyncClient()` inside a function, which pays the
connection-pool setup cost on every call and never shares a pool across
features. One shared client per process amortises TCP + TLS reuse.

Adapters should take an `httpx.AsyncClient` as a dependency; `get_http()`
pulls the shared instance from `app.state`.
"""
from __future__ import annotations

import httpx
from fastapi import Request


def build_shared_client() -> httpx.AsyncClient:
    """Build the one process-wide client. Call from lifespan startup."""
    # Timeouts matter — the fmcsa paginator has its own soft budget, the
    # Overpass source its own, so we pick a generous conservative default.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        http2=False,
    )


def get_http(request: Request) -> httpx.AsyncClient:
    """FastAPI dep — hand back the shared client."""
    return request.app.state.http  # type: ignore[no-any-return]
