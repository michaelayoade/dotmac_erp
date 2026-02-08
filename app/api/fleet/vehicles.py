"""
Vehicle API Endpoints.

REST API for vehicle management.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.fleet.enums import (
    AssignmentType,
    OwnershipType,
    VehicleStatus,
    VehicleType,
)
from app.schemas.fleet.vehicle import (
    FleetSummary,
    OdometerUpdate,
    VehicleBrief,
    VehicleCreate,
    VehicleDispose,
    VehicleListResponse,
    VehicleRead,
    VehicleStatusChange,
    VehicleUpdate,
)
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginationParams,
    ValidationError,
)
from app.services.fleet.vehicle_service import VehicleService

router = APIRouter(prefix="/vehicles", tags=["fleet-vehicles"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=VehicleListResponse)
def list_vehicles(
    organization_id: UUID = Depends(require_organization_id),
    status: str | None = None,
    vehicle_type: str | None = None,
    assignment_type: str | None = None,
    ownership_type: str | None = None,
    assigned_employee_id: UUID | None = None,
    search: str | None = None,
    include_disposed: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all vehicles with optional filtering."""
    # Parse enums
    status_enum = VehicleStatus(status) if status else None
    type_enum = VehicleType(vehicle_type) if vehicle_type else None
    assignment_enum = AssignmentType(assignment_type) if assignment_type else None
    ownership_enum = OwnershipType(ownership_type) if ownership_type else None

    service = VehicleService(db, organization_id)
    result = service.list_vehicles(
        status=status_enum,
        vehicle_type=type_enum,
        assignment_type=assignment_enum,
        ownership_type=ownership_enum,
        assigned_employee_id=assigned_employee_id,
        search=search,
        include_disposed=include_disposed,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return VehicleListResponse(
        items=[VehicleBrief.model_validate(v) for v in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/summary", response_model=FleetSummary)
def get_fleet_summary(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get overall fleet statistics."""
    service = VehicleService(db, organization_id)
    summary = service.get_fleet_summary()
    return FleetSummary(**summary)


@router.get("/{vehicle_id}", response_model=VehicleRead)
def get_vehicle(
    vehicle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get vehicle details by ID."""
    service = VehicleService(db, organization_id)
    try:
        vehicle = service.get_or_raise(vehicle_id)
        return VehicleRead.model_validate(vehicle)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
def create_vehicle(
    data: VehicleCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Add a new vehicle to the fleet."""
    service = VehicleService(db, organization_id)
    try:
        vehicle = service.create(data)
        db.commit()
        return VehicleRead.model_validate(vehicle)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{vehicle_id}", response_model=VehicleRead)
def update_vehicle(
    vehicle_id: UUID,
    data: VehicleUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update vehicle details."""
    service = VehicleService(db, organization_id)
    try:
        vehicle = service.update(vehicle_id, data)
        db.commit()
        return VehicleRead.model_validate(vehicle)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValidationError, ConflictError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{vehicle_id}/status", response_model=VehicleRead)
def change_vehicle_status(
    vehicle_id: UUID,
    data: VehicleStatusChange,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Change vehicle operational status."""
    service = VehicleService(db, organization_id)
    try:
        vehicle = service.change_status(vehicle_id, data.status, data.reason)
        db.commit()
        return VehicleRead.model_validate(vehicle)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{vehicle_id}/odometer", response_model=VehicleRead)
def update_odometer(
    vehicle_id: UUID,
    data: OdometerUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record new odometer reading."""
    service = VehicleService(db, organization_id)
    try:
        vehicle = service.update_odometer(vehicle_id, data.reading, data.reading_date)
        db.commit()
        return VehicleRead.model_validate(vehicle)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{vehicle_id}/dispose", response_model=VehicleRead)
def dispose_vehicle(
    vehicle_id: UUID,
    data: VehicleDispose,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Dispose of a vehicle (sell, scrap, trade-in)."""
    service = VehicleService(db, organization_id)
    try:
        vehicle = service.dispose(vehicle_id, data.method, data.amount, data.notes)
        db.commit()
        return VehicleRead.model_validate(vehicle)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vehicle(
    vehicle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Soft delete a vehicle."""
    service = VehicleService(db, organization_id)
    try:
        service.soft_delete(vehicle_id)
        db.commit()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
