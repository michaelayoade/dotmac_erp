"""
Fuel Log API Endpoints.

REST API for fuel log management.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.schemas.fleet.fuel import (
    FuelEfficiencyReport,
    FuelLogBrief,
    FuelLogCreate,
    FuelLogListResponse,
    FuelLogRead,
    FuelLogUpdate,
)
from app.services.common import NotFoundError, PaginationParams, ValidationError
from app.services.fleet.fuel_service import FuelService

router = APIRouter(prefix="/fuel", tags=["fleet-fuel"])


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("", response_model=FuelLogListResponse)
def list_fuel_logs(
    organization_id: UUID = Depends(require_organization_id),
    vehicle_id: UUID | None = None,
    employee_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List fuel log entries with optional filtering."""
    service = FuelService(db, organization_id)
    result = service.list_logs(
        vehicle_id=vehicle_id,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return FuelLogListResponse(
        items=[FuelLogBrief.model_validate(l) for l in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/efficiency/{vehicle_id}", response_model=FuelEfficiencyReport | None)
def get_fuel_efficiency(
    vehicle_id: UUID,
    from_date: date | None = None,
    to_date: date | None = None,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Calculate fuel efficiency for a vehicle."""
    service = FuelService(db, organization_id)
    return service.calculate_efficiency(vehicle_id, from_date, to_date)


@router.get("/{fuel_log_id}", response_model=FuelLogRead)
def get_fuel_log(
    fuel_log_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get fuel log details."""
    service = FuelService(db, organization_id)
    try:
        log = service.get_or_raise(fuel_log_id)
        return FuelLogRead.model_validate(log)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("", response_model=FuelLogRead, status_code=status.HTTP_201_CREATED)
def create_fuel_log(
    data: FuelLogCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record a new fuel purchase."""
    service = FuelService(db, organization_id)
    try:
        log = service.create(data)
        return FuelLogRead.model_validate(log)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{fuel_log_id}", response_model=FuelLogRead)
def update_fuel_log(
    fuel_log_id: UUID,
    data: FuelLogUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a fuel log entry."""
    service = FuelService(db, organization_id)
    try:
        log = service.update(fuel_log_id, data)
        return FuelLogRead.model_validate(log)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{fuel_log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fuel_log(
    fuel_log_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a fuel log entry."""
    service = FuelService(db, organization_id)
    try:
        service.delete(fuel_log_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
