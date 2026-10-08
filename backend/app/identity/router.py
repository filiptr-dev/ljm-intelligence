"""identity router — aggregator. Includes auth + settings sub-routers.

Keeps the module boundary at one `router.py` per the architecture plan.
Imports are explicit; URL prefixes are owned by the sub-routers.
"""
from fastapi import APIRouter

from app.identity.auth_router import router as _auth_router
from app.identity.settings_router import router as _settings_router

router = APIRouter()
router.include_router(_auth_router)
router.include_router(_settings_router)
