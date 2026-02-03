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
from fastapi import APIRouter

from app.api.fleet.vehicles import router as vehicles_router
from app.api.fleet.maintenance import router as maintenance_router
from app.api.fleet.fuel import router as fuel_router
from app.api.fleet.incidents import router as incidents_router
from app.api.fleet.documents import router as documents_router
from app.api.fleet.reservations import router as reservations_router
from app.api.fleet.assignments import router as assignments_router

router = APIRouter(prefix="/fleet", tags=["fleet"])

router.include_router(vehicles_router)
router.include_router(maintenance_router)
router.include_router(fuel_router)
router.include_router(incidents_router)
router.include_router(documents_router)
router.include_router(reservations_router)
router.include_router(assignments_router)

__all__ = ["router"]
