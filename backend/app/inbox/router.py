"""inbox router — aggregator. Includes mail + inbox sub-routers."""
from fastapi import APIRouter

from app.inbox.inbox_router import router as _inbox_router
from app.inbox.mail_router import cron_router as _mail_cron_router, router as _mail_router

router = APIRouter()
router.include_router(_inbox_router)
router.include_router(_mail_router)
router.include_router(_mail_cron_router)
