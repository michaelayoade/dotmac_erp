"""
Reservation API Endpoints.

REST API for pool vehicle reservation management.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.fleet.enums import ReservationStatus
from app.schemas.fleet.reservation import (
    AvailableVehiclesRequest,
    ReservationApprove,
    ReservationBrief,
    ReservationCheckin,
    ReservationCheckout,
    ReservationCreate,
    ReservationListResponse,
    ReservationRead,
    ReservationReject,
    ReservationUpdate,
)
from app.schemas.fleet.vehicle import VehicleBrief
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginationParams,
    ValidationError,
)
from app.services.fleet.reservation_service import ReservationService
from app.services.fleet.vehicle_service import VehicleService

router = APIRouter(prefix="/reservations", tags=["fleet-reservations"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=ReservationListResponse)
def list_reservations(
    organization_id: UUID = Depends(require_organization_id),
    vehicle_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    status: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List reservations with optional filtering."""
    status_enum = ReservationStatus(status) if status else None

    service = ReservationService(db, organization_id)
    result = service.list_reservations(
        vehicle_id=vehicle_id,
        employee_id=employee_id,
        status=status_enum,
        from_date=from_date,
        to_date=to_date,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return ReservationListResponse(
        items=[ReservationBrief.model_validate(r) for r in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.post("/available", response_model=List[VehicleBrief])
def get_available_vehicles(
    data: AvailableVehiclesRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get pool vehicles available for the requested time period."""
    service = VehicleService(db, organization_id)
    vehicles = service.get_available_pool_vehicles(
        data.start_datetime, data.end_datetime
    )
    return [VehicleBrief.model_validate(v) for v in vehicles]


@router.get("/{reservation_id}", response_model=ReservationRead)
def get_reservation(
    reservation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get reservation details."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.get_or_raise(reservation_id)
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("", response_model=ReservationRead, status_code=status.HTTP_201_CREATED)
def create_reservation(
    data: ReservationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a new reservation request."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.create(data)
        db.commit()
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.patch("/{reservation_id}", response_model=ReservationRead)
def update_reservation(
    reservation_id: UUID,
    data: ReservationUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a reservation."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.update(reservation_id, data)
        db.commit()
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{reservation_id}/approve", response_model=ReservationRead)
def approve_reservation(
    reservation_id: UUID,
    data: ReservationApprove,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Approve a reservation request."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.approve(reservation_id, data.approved_by_id)
        db.commit()
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{reservation_id}/reject", response_model=ReservationRead)
def reject_reservation(
    reservation_id: UUID,
    data: ReservationReject,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Reject a reservation request."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.reject(reservation_id, data.rejection_reason)
        db.commit()
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{reservation_id}/checkout", response_model=ReservationRead)
def checkout_vehicle(
    reservation_id: UUID,
    data: ReservationCheckout,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Check out vehicle (start the reservation)."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.checkout(reservation_id, data)
        db.commit()
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{reservation_id}/checkin", response_model=ReservationRead)
def checkin_vehicle(
    reservation_id: UUID,
    data: ReservationCheckin,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Check in vehicle (complete the reservation)."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.checkin(reservation_id, data)
        db.commit()
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{reservation_id}/cancel", response_model=ReservationRead)
def cancel_reservation(
    reservation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Cancel a reservation."""
    service = ReservationService(db, organization_id)
    try:
        reservation = service.cancel(reservation_id)
        db.commit()
        return ReservationRead.model_validate(reservation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
