"""
Fleet Management API.

REST API endpoints for:
- Vehicles
- Maintenance
- Fuel logs
- Incidents
- Documents
- Reservations
- Assignments
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_tenant_permission
from app.api.fleet.assignments import router as assignments_router
from app.api.fleet.documents import router as documents_router
from app.api.fleet.fuel import router as fuel_router
from app.api.fleet.import_export import router as import_router
from app.api.fleet.incidents import router as incidents_router
from app.api.fleet.maintenance import router as maintenance_router
from app.api.fleet.reservations import router as reservations_router
from app.api.fleet.vehicles import router as vehicles_router

router = APIRouter(
    prefix="/fleet",
    tags=["fleet"],
    dependencies=[Depends(require_tenant_permission("fleet:access"))],
)

router.include_router(
    vehicles_router,
    dependencies=[Depends(require_tenant_permission("fleet:vehicles:manage"))],
)
router.include_router(
    maintenance_router,
    dependencies=[Depends(require_tenant_permission("fleet:maintenance:read"))],
)
router.include_router(
    fuel_router,
    dependencies=[Depends(require_tenant_permission("fleet:fuel:read"))],
)
router.include_router(
    incidents_router,
    dependencies=[Depends(require_tenant_permission("fleet:incidents:read"))],
)
router.include_router(
    import_router,
    dependencies=[Depends(require_tenant_permission("fleet:imports:manage"))],
)
router.include_router(
    documents_router,
    dependencies=[Depends(require_tenant_permission("fleet:documents:manage"))],
)
router.include_router(
    reservations_router,
    dependencies=[Depends(require_tenant_permission("fleet:reservations:manage"))],
)
router.include_router(
    assignments_router,
    dependencies=[Depends(require_tenant_permission("fleet:vehicles:manage"))],
)

__all__ = ["router"]
