"""
Incident API Endpoints.

REST API for vehicle incident management.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.fleet.enums import IncidentSeverity, IncidentStatus, IncidentType
from app.schemas.fleet.incident import (
    IncidentBrief,
    IncidentCreate,
    IncidentListResponse,
    IncidentRead,
    IncidentResolve,
    IncidentUpdate,
)
from app.services.common import NotFoundError, PaginationParams, ValidationError
from app.services.fleet.incident_service import IncidentService

router = APIRouter(prefix="/incidents", tags=["fleet-incidents"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=IncidentListResponse)
def list_incidents(
    organization_id: UUID = Depends(require_organization_id),
    vehicle_id: UUID | None = None,
    driver_id: UUID | None = None,
    status: str | None = None,
    incident_type: str | None = None,
    severity: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List incidents with optional filtering."""
    status_enum = IncidentStatus(status) if status else None
    type_enum = IncidentType(incident_type) if incident_type else None
    severity_enum = IncidentSeverity(severity) if severity else None

    service = IncidentService(db, organization_id)
    result = service.list_incidents(
        vehicle_id=vehicle_id,
        driver_id=driver_id,
        status=status_enum,
        incident_type=type_enum,
        severity=severity_enum,
        from_date=from_date,
        to_date=to_date,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return IncidentListResponse(
        items=[IncidentBrief.model_validate(i) for i in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(
    incident_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get incident details."""
    service = IncidentService(db, organization_id)
    try:
        incident = service.get_or_raise(incident_id)
        return IncidentRead.model_validate(incident)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(
    data: IncidentCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Report a new incident."""
    service = IncidentService(db, organization_id)
    try:
        incident = service.create(data)
        db.commit()
        return IncidentRead.model_validate(incident)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{incident_id}", response_model=IncidentRead)
def update_incident(
    incident_id: UUID,
    data: IncidentUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update incident details."""
    service = IncidentService(db, organization_id)
    try:
        incident = service.update(incident_id, data)
        db.commit()
        return IncidentRead.model_validate(incident)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{incident_id}/resolve", response_model=IncidentRead)
def resolve_incident(
    incident_id: UUID,
    data: IncidentResolve,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Resolve an incident."""
    service = IncidentService(db, organization_id)
    try:
        incident = service.resolve(incident_id, data)
        db.commit()
        return IncidentRead.model_validate(incident)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{incident_id}/close", response_model=IncidentRead)
def close_incident(
    incident_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Close an incident."""
    service = IncidentService(db, organization_id)
    try:
        incident = service.close(incident_id)
        db.commit()
        return IncidentRead.model_validate(incident)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_incident(
    incident_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Soft delete an incident."""
    service = IncidentService(db, organization_id)
    try:
        service.soft_delete(incident_id)
        db.commit()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
