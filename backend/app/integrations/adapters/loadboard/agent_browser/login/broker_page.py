"""Public broker page — no login.

Shape-compatible with the gated-source modules so the dispatcher in
:mod:`..driver` can keep one call signature. The ``login`` here is a
noop: a public broker page is reached by a plain ``navigate`` and the
agent loop reads what's visible.
"""

from __future__ import annotations

from typing import Any


async def login(page: Any, credentials: dict | None = None) -> dict:
    # Nothing to do — return an empty storage_state so the dispatcher can
    # treat the return shape uniformly.
    return {"cookies": [], "origins": []}


__all__ = ["login"]
