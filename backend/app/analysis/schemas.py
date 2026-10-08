"""analysis — DTOs (pydantic request/response models).

Added by the 2026-10-08 onion/SOLID refactor. New router↔service shapes
live here instead of being hand-rolled per endpoint. Existing routers
keep their inline pydantic models for now; this file is the home for
new shared DTOs. See the plan: projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md.
"""
from __future__ import annotations
