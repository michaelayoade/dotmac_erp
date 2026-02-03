"""
Vehicle Reservation Pydantic Schemas.

Schemas for pool vehicle reservation API endpoints.
"""
from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import ReservationStatus


class ReservationBase(BaseModel):
    """Base reservation schema."""

    vehicle_id: UUID
    start_datetime: datetime
    end_datetime: datetime
    purpose: str = Field(max_length=500)
    destination: Optional[str] = Field(default=None, max_length=300)
    estimated_distance_km: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = None


class ReservationCreate(ReservationBase):
    """Create reservation request."""

    employee_id: UUID


class ReservationUpdate(BaseModel):
    """Update reservation request."""

    vehicle_id: Optional[UUID] = None
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    purpose: Optional[str] = Field(default=None, max_length=500)
    destination: Optional[str] = Field(default=None, max_length=300)
    estimated_distance_km: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = None


class ReservationRead(ReservationBase):
    """Reservation response."""

    model_config = ConfigDict(from_attributes=True)

    reservation_id: UUID
    organization_id: UUID
    employee_id: UUID
    status: ReservationStatus
    actual_start_datetime: Optional[datetime] = None
    actual_end_datetime: Optional[datetime] = None
    approved_by_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    start_odometer: Optional[int] = None
    end_odometer: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class ReservationBrief(BaseModel):
    """Brief reservation summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    reservation_id: UUID
    vehicle_id: UUID
    employee_id: UUID
    start_datetime: datetime
    end_datetime: datetime
    purpose: str
    status: ReservationStatus


class ReservationWithDetails(ReservationRead):
    """Reservation with related data."""

    model_config = ConfigDict(from_attributes=True)

    employee_name: Optional[str] = None
    vehicle_name: Optional[str] = None
    approved_by_name: Optional[str] = None
    duration_hours: Optional[float] = None
    actual_duration_hours: Optional[float] = None
    actual_distance_km: Optional[int] = None


class ReservationApprove(BaseModel):
    """Request to approve a reservation."""

    approved_by_id: UUID


class ReservationReject(BaseModel):
    """Request to reject a reservation."""

    rejection_reason: str = Field(max_length=300)


class ReservationCheckout(BaseModel):
    """Request to check out (start using) a vehicle."""

    actual_start_datetime: Optional[datetime] = None
    start_odometer: int = Field(ge=0)


class ReservationCheckin(BaseModel):
    """Request to check in (return) a vehicle."""

    actual_end_datetime: Optional[datetime] = None
    end_odometer: int = Field(ge=0)
    notes: Optional[str] = None


class ReservationListResponse(BaseModel):
    """Paginated reservation list response."""

    items: List[ReservationBrief]
    total: int
    offset: int
    limit: int


class AvailableVehiclesRequest(BaseModel):
    """Request to find available vehicles for a time period."""

    start_datetime: datetime
    end_datetime: datetime
