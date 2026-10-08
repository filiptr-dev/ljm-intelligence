"""outreach router — aggregator. Includes call-list, capacity, email, unsubscribe."""
from fastapi import APIRouter

from app.outreach.call_list_router import router as _call_list_router
from app.outreach.capacity_router import router as _capacity_router
from app.outreach.email_router import router as _email_router
from app.outreach.unsub_router import unsub_router as _unsub_router

router = APIRouter()
router.include_router(_call_list_router)
router.include_router(_capacity_router)
router.include_router(_email_router)
router.include_router(_unsub_router)
