"""
Procurement Management API.

REST API endpoints for:
- Procurement plans
- Requisitions
- RFQs
- Quotation responses
- Bid evaluations
- Contracts
- Vendor prequalification
"""

from fastapi import APIRouter

from app.api.procurement.contracts import router as contracts_router
from app.api.procurement.evaluations import router as evaluations_router
from app.api.procurement.plans import router as plans_router
from app.api.procurement.quotations import router as quotations_router
from app.api.procurement.requisitions import router as requisitions_router
from app.api.procurement.rfqs import router as rfqs_router
from app.api.procurement.vendors import router as vendors_router

router = APIRouter(prefix="/procurement", tags=["procurement"])

router.include_router(plans_router)
router.include_router(requisitions_router)
router.include_router(rfqs_router)
router.include_router(quotations_router)
router.include_router(evaluations_router)
router.include_router(contracts_router)
router.include_router(vendors_router)

__all__ = ["router"]
