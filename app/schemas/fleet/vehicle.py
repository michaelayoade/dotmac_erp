"""
Vehicle Pydantic Schemas.

Schemas for Vehicle API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import (
    AssignmentType,
    DisposalMethod,
    FuelType,
    OwnershipType,
    VehicleStatus,
    VehicleType,
)


# =============================================================================
# Vehicle Schemas
# =============================================================================


class VehicleBase(BaseModel):
    """Base vehicle schema."""

    vehicle_code: str = Field(max_length=30)
    registration_number: str = Field(max_length=20)
    vin: Optional[str] = Field(default=None, max_length=50)
    engine_number: Optional[str] = Field(default=None, max_length=50)

    # Specifications
    make: str = Field(max_length=50)
    model: str = Field(max_length=50)
    year: int = Field(ge=1900, le=2100)
    color: Optional[str] = Field(default=None, max_length=30)
    vehicle_type: VehicleType = VehicleType.SEDAN
    fuel_type: FuelType = FuelType.PETROL
    transmission: Optional[str] = Field(default=None, max_length=20)
    engine_capacity_cc: Optional[int] = Field(default=None, ge=0)
    seating_capacity: int = Field(default=5, ge=1)
    fuel_tank_capacity_liters: Optional[Decimal] = Field(default=None, ge=0)
    expected_fuel_efficiency: Optional[Decimal] = Field(default=None, ge=0)

    # Ownership
    ownership_type: OwnershipType = OwnershipType.OWNED
    purchase_date: Optional[date] = None
    purchase_price: Optional[Decimal] = Field(default=None, ge=0)
    lease_start_date: Optional[date] = None
    lease_end_date: Optional[date] = None
    lease_monthly_cost: Optional[Decimal] = Field(default=None, ge=0)
    vendor_id: Optional[UUID] = None

    # Assignment
    assignment_type: AssignmentType = AssignmentType.POOL
    assigned_employee_id: Optional[UUID] = None
    assigned_department_id: Optional[UUID] = None
    assigned_cost_center_id: Optional[UUID] = None

    # GPS
    has_gps_tracker: bool = False
    gps_device_id: Optional[str] = Field(default=None, max_length=50)

    notes: Optional[str] = None


class VehicleCreate(VehicleBase):
    """Create vehicle request."""

    current_odometer: int = Field(default=0, ge=0)


class VehicleUpdate(BaseModel):
    """Update vehicle request."""

    vehicle_code: Optional[str] = Field(default=None, max_length=30)
    registration_number: Optional[str] = Field(default=None, max_length=20)
    vin: Optional[str] = Field(default=None, max_length=50)
    engine_number: Optional[str] = Field(default=None, max_length=50)
    color: Optional[str] = Field(default=None, max_length=30)
    vehicle_type: Optional[VehicleType] = None
    fuel_type: Optional[FuelType] = None
    transmission: Optional[str] = Field(default=None, max_length=20)
    engine_capacity_cc: Optional[int] = Field(default=None, ge=0)
    seating_capacity: Optional[int] = Field(default=None, ge=1)
    fuel_tank_capacity_liters: Optional[Decimal] = Field(default=None, ge=0)
    expected_fuel_efficiency: Optional[Decimal] = Field(default=None, ge=0)
    ownership_type: Optional[OwnershipType] = None
    purchase_date: Optional[date] = None
    purchase_price: Optional[Decimal] = Field(default=None, ge=0)
    lease_start_date: Optional[date] = None
    lease_end_date: Optional[date] = None
    lease_monthly_cost: Optional[Decimal] = Field(default=None, ge=0)
    vendor_id: Optional[UUID] = None
    assignment_type: Optional[AssignmentType] = None
    assigned_employee_id: Optional[UUID] = None
    assigned_department_id: Optional[UUID] = None
    assigned_cost_center_id: Optional[UUID] = None
    has_gps_tracker: Optional[bool] = None
    gps_device_id: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = None


class VehicleRead(VehicleBase):
    """Vehicle response."""

    model_config = ConfigDict(from_attributes=True)

    vehicle_id: UUID
    organization_id: UUID
    status: VehicleStatus
    current_odometer: int
    last_odometer_date: Optional[date] = None
    last_known_location: Optional[str] = None
    last_location_update: Optional[datetime] = None
    disposal_date: Optional[date] = None
    disposal_method: Optional[DisposalMethod] = None
    disposal_amount: Optional[Decimal] = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None


class VehicleBrief(BaseModel):
    """Brief vehicle summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    vehicle_id: UUID
    vehicle_code: str
    registration_number: str
    make: str
    model: str
    year: int
    vehicle_type: VehicleType
    status: VehicleStatus
    assignment_type: AssignmentType
    current_odometer: int


class VehicleWithDetails(VehicleRead):
    """Vehicle with related data."""

    model_config = ConfigDict(from_attributes=True)

    # Computed from relationships
    assigned_employee_name: Optional[str] = None
    assigned_department_name: Optional[str] = None
    active_documents_count: int = 0
    expiring_documents_count: int = 0
    pending_maintenance_count: int = 0
    open_incidents_count: int = 0

    # Computed properties
    display_name: Optional[str] = None
    age_years: Optional[int] = None


class VehicleListResponse(BaseModel):
    """Paginated vehicle list response."""

    items: List[VehicleBrief]
    total: int
    offset: int
    limit: int


# =============================================================================
# Fleet Summary Schema
# =============================================================================


class FleetSummary(BaseModel):
    """Fleet statistics summary."""

    total_vehicles: int
    active: int
    in_maintenance: int
    out_of_service: int
    disposed: int
    owned_count: int
    leased_count: int
    total_owned_value: Decimal
    monthly_lease_cost: Decimal
    avg_age_years: float


# =============================================================================
# Action Schemas
# =============================================================================


class VehicleStatusChange(BaseModel):
    """Request to change vehicle status."""

    status: VehicleStatus
    reason: Optional[str] = Field(default=None, max_length=200)


class OdometerUpdate(BaseModel):
    """Request to update odometer reading."""

    reading: int = Field(ge=0)
    reading_date: Optional[date] = None


class VehicleDispose(BaseModel):
    """Request to dispose of vehicle."""

    method: DisposalMethod
    amount: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None
