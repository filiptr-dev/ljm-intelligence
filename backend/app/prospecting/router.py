"""prospecting router — aggregator. Includes leads/brokers/shippers/crawl/enrichment."""
from fastapi import APIRouter

from app.prospecting.brokers_router import router as _brokers_router
from app.prospecting.contacts_router import router as _contacts_router
from app.prospecting.crawl_router import router as _crawl_router
from app.prospecting.enrichment_router import router as _enrichment_router
from app.prospecting.leads_router import router as _leads_router
from app.prospecting.shipper_finder_router import router as _shipper_finder_router

router = APIRouter()
router.include_router(_leads_router)
router.include_router(_brokers_router)
router.include_router(_shipper_finder_router)
router.include_router(_crawl_router)
router.include_router(_enrichment_router)
router.include_router(_contacts_router)
