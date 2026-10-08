"""analysis router — aggregator. Includes ai + overview + analysis sub-routers."""
from fastapi import APIRouter

from app.analysis.ai_router import router as _ai_router
from app.analysis.analysis_router import router as _analysis_router
from app.analysis.overview_router import router as _overview_router

router = APIRouter()
router.include_router(_ai_router)
router.include_router(_overview_router)
router.include_router(_analysis_router)
