"""
Maintenance API Endpoints.

REST API for vehicle maintenance management.
"""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.fleet.enums import MaintenanceStatus, MaintenanceType
from app.schemas.fleet.maintenance import (
    MaintenanceBrief,
    MaintenanceComplete,
    MaintenanceCreate,
    MaintenanceListResponse,
    MaintenanceRead,
    MaintenanceUpdate,
)
from app.services.common import NotFoundError, PaginationParams, ValidationError
from app.services.fleet.maintenance_service import MaintenanceService

router = APIRouter(prefix="/maintenance", tags=["fleet-maintenance"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=MaintenanceListResponse)
def list_maintenance(
    organization_id: UUID = Depends(require_organization_id),
    vehicle_id: Optional[UUID] = None,
    status: Optional[str] = None,
    maintenance_type: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List maintenance records with optional filtering."""
    status_enum = MaintenanceStatus(status) if status else None
    type_enum = MaintenanceType(maintenance_type) if maintenance_type else None

    service = MaintenanceService(db, organization_id)
    result = service.list_records(
        vehicle_id=vehicle_id,
        status=status_enum,
        maintenance_type=type_enum,
        from_date=from_date,
        to_date=to_date,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return MaintenanceListResponse(
        items=[MaintenanceBrief.model_validate(r) for r in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/{maintenance_id}", response_model=MaintenanceRead)
def get_maintenance(
    maintenance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get maintenance record details."""
    service = MaintenanceService(db, organization_id)
    try:
        record = service.get_or_raise(maintenance_id)
        return MaintenanceRead.model_validate(record)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("", response_model=MaintenanceRead, status_code=status.HTTP_201_CREATED)
def create_maintenance(
    data: MaintenanceCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Schedule a new maintenance record."""
    service = MaintenanceService(db, organization_id)
    try:
        record = service.create(data)
        db.commit()
        return MaintenanceRead.model_validate(record)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{maintenance_id}", response_model=MaintenanceRead)
def update_maintenance(
    maintenance_id: UUID,
    data: MaintenanceUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a maintenance record."""
    service = MaintenanceService(db, organization_id)
    try:
        record = service.update(maintenance_id, data)
        db.commit()
        return MaintenanceRead.model_validate(record)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{maintenance_id}/start", response_model=MaintenanceRead)
def start_maintenance(
    maintenance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Mark maintenance as in progress."""
    service = MaintenanceService(db, organization_id)
    try:
        record = service.start(maintenance_id)
        db.commit()
        return MaintenanceRead.model_validate(record)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{maintenance_id}/complete", response_model=MaintenanceRead)
def complete_maintenance(
    maintenance_id: UUID,
    data: MaintenanceComplete,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Complete a maintenance record."""
    service = MaintenanceService(db, organization_id)
    try:
        record = service.complete(maintenance_id, data)
        db.commit()
        return MaintenanceRead.model_validate(record)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{maintenance_id}/cancel", response_model=MaintenanceRead)
def cancel_maintenance(
    maintenance_id: UUID,
    reason: Optional[str] = None,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Cancel a maintenance record."""
    service = MaintenanceService(db, organization_id)
    try:
        record = service.cancel(maintenance_id, reason)
        db.commit()
        return MaintenanceRead.model_validate(record)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
