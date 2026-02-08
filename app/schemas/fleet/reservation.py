"""
Vehicle Reservation Pydantic Schemas.

Schemas for pool vehicle reservation API endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import ReservationStatus


class ReservationBase(BaseModel):
    """Base reservation schema."""

    vehicle_id: UUID
    start_datetime: datetime
    end_datetime: datetime
    purpose: str = Field(max_length=500)
    destination: str | None = Field(default=None, max_length=300)
    estimated_distance_km: int | None = Field(default=None, ge=0)
    notes: str | None = None


class ReservationCreate(ReservationBase):
    """Create reservation request."""

    employee_id: UUID


class ReservationUpdate(BaseModel):
    """Update reservation request."""

    vehicle_id: UUID | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    purpose: str | None = Field(default=None, max_length=500)
    destination: str | None = Field(default=None, max_length=300)
    estimated_distance_km: int | None = Field(default=None, ge=0)
    notes: str | None = None


class ReservationRead(ReservationBase):
    """Reservation response."""

    model_config = ConfigDict(from_attributes=True)

    reservation_id: UUID
    organization_id: UUID
    employee_id: UUID
    status: ReservationStatus
    actual_start_datetime: datetime | None = None
    actual_end_datetime: datetime | None = None
    approved_by_id: UUID | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    start_odometer: int | None = None
    end_odometer: int | None = None
    created_at: datetime
    updated_at: datetime | None = None


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

    employee_name: str | None = None
    vehicle_name: str | None = None
    approved_by_name: str | None = None
    duration_hours: float | None = None
    actual_duration_hours: float | None = None
    actual_distance_km: int | None = None


class ReservationApprove(BaseModel):
    """Request to approve a reservation."""

    approved_by_id: UUID


class ReservationReject(BaseModel):
    """Request to reject a reservation."""

    rejection_reason: str = Field(max_length=300)


class ReservationCheckout(BaseModel):
    """Request to check out (start using) a vehicle."""

    actual_start_datetime: datetime | None = None
    start_odometer: int = Field(ge=0)


class ReservationCheckin(BaseModel):
    """Request to check in (return) a vehicle."""

    actual_end_datetime: datetime | None = None
    end_odometer: int = Field(ge=0)
    notes: str | None = None


class ReservationListResponse(BaseModel):
    """Paginated reservation list response."""

    items: list[ReservationBrief]
    total: int
    offset: int
    limit: int


class AvailableVehiclesRequest(BaseModel):
    """Request to find available vehicles for a time period."""

    start_datetime: datetime
    end_datetime: datetime
