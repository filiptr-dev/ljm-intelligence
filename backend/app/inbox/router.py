"""inbox router — thin HTTP surface. Full routes land during per-module modularisation.

See projects/ljm-intelligence/plan/2026-10-01-architecture-foundation-tenant-ready.md.
"""
from fastapi import APIRouter

router = APIRouter()
