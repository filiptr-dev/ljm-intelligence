"""integrations router — aggregator. Includes loads + jobs sub-routers."""
from fastapi import APIRouter

from app.integrations.jobs_router import admin_router as _jobs_admin_router, router as _jobs_router
from app.integrations.loads_router import router as _loads_router

router = APIRouter()
router.include_router(_loads_router)
router.include_router(_jobs_router)
# jobs_admin_router kept separate — main.py mounts it with user-only deps.
