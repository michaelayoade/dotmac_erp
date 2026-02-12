"""
Vehicle Pydantic Schemas.

Schemas for Vehicle API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
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
    vin: str | None = Field(default=None, max_length=50)
    engine_number: str | None = Field(default=None, max_length=50)

    # Specifications
    make: str = Field(max_length=50)
    model: str = Field(max_length=50)
    year: int = Field(ge=1900, le=2100)
    color: str | None = Field(default=None, max_length=30)
    vehicle_type: VehicleType = VehicleType.SEDAN
    fuel_type: FuelType = FuelType.PETROL
    transmission: str | None = Field(default=None, max_length=20)
    engine_capacity_cc: int | None = Field(default=None, ge=0)
    seating_capacity: int = Field(default=5, ge=1)
    fuel_tank_capacity_liters: Decimal | None = Field(default=None, ge=0)
    expected_fuel_efficiency: Decimal | None = Field(default=None, ge=0)

    # Ownership
    ownership_type: OwnershipType = OwnershipType.OWNED
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0)
    lease_start_date: date | None = None
    lease_end_date: date | None = None
    lease_monthly_cost: Decimal | None = Field(default=None, ge=0)
    vendor_id: UUID | None = None
    license_expiry_date: date | None = None
    location_id: UUID | None = None

    # Assignment
    assignment_type: AssignmentType = AssignmentType.POOL
    assigned_employee_id: UUID | None = None
    assigned_department_id: UUID | None = None
    assigned_cost_center_id: UUID | None = None

    # GPS
    has_gps_tracker: bool = False
    gps_device_id: str | None = Field(default=None, max_length=50)

    notes: str | None = None


class VehicleCreate(VehicleBase):
    """Create vehicle request."""

    current_odometer: int = Field(default=0, ge=0)


class VehicleUpdate(BaseModel):
    """Update vehicle request."""

    vehicle_code: str | None = Field(default=None, max_length=30)
    registration_number: str | None = Field(default=None, max_length=20)
    vin: str | None = Field(default=None, max_length=50)
    engine_number: str | None = Field(default=None, max_length=50)
    color: str | None = Field(default=None, max_length=30)
    vehicle_type: VehicleType | None = None
    fuel_type: FuelType | None = None
    transmission: str | None = Field(default=None, max_length=20)
    engine_capacity_cc: int | None = Field(default=None, ge=0)
    seating_capacity: int | None = Field(default=None, ge=1)
    fuel_tank_capacity_liters: Decimal | None = Field(default=None, ge=0)
    expected_fuel_efficiency: Decimal | None = Field(default=None, ge=0)
    ownership_type: OwnershipType | None = None
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0)
    lease_start_date: date | None = None
    lease_end_date: date | None = None
    lease_monthly_cost: Decimal | None = Field(default=None, ge=0)
    vendor_id: UUID | None = None
    license_expiry_date: date | None = None
    location_id: UUID | None = None
    assignment_type: AssignmentType | None = None
    assigned_employee_id: UUID | None = None
    assigned_department_id: UUID | None = None
    assigned_cost_center_id: UUID | None = None
    has_gps_tracker: bool | None = None
    gps_device_id: str | None = Field(default=None, max_length=50)
    notes: str | None = None


class VehicleRead(VehicleBase):
    """Vehicle response."""

    model_config = ConfigDict(from_attributes=True)

    vehicle_id: UUID
    organization_id: UUID
    status: VehicleStatus
    current_odometer: int
    last_odometer_date: date | None = None
    last_known_location: str | None = None
    last_location_update: datetime | None = None
    disposal_date: date | None = None
    disposal_method: DisposalMethod | None = None
    disposal_amount: Decimal | None = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime | None = None


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
    assigned_employee_name: str | None = None
    assigned_department_name: str | None = None
    active_documents_count: int = 0
    expiring_documents_count: int = 0
    pending_maintenance_count: int = 0
    open_incidents_count: int = 0

    # Computed properties
    display_name: str | None = None
    age_years: int | None = None


class VehicleListResponse(BaseModel):
    """Paginated vehicle list response."""

    items: list[VehicleBrief]
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
    reason: str | None = Field(default=None, max_length=200)


class OdometerUpdate(BaseModel):
    """Request to update odometer reading."""

    reading: int = Field(ge=0)
    reading_date: date | None = None


class VehicleDispose(BaseModel):
    """Request to dispose of vehicle."""

    method: DisposalMethod
    amount: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None
