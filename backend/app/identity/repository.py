"""identity — repository seam.

The SQL seam the architecture plan promises: one repo class per aggregate,
each service depends on the Protocol (not AsyncSession), and the concrete
SqlAlchemy implementation lives at the bottom. Services that still query
directly today are being migrated here one aggregate at a time; nothing
new should bypass this seam.

See projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md.
"""
from __future__ import annotations

from typing import Protocol  # noqa: F401
