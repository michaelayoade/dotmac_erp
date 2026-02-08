"""
Fuel Log Pydantic Schemas.

Schemas for fuel log API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import FuelType


class FuelLogBase(BaseModel):
    """Base fuel log schema."""

    vehicle_id: UUID
    log_date: date
    fuel_type: FuelType
    quantity_liters: Decimal = Field(gt=0)
    price_per_liter: Decimal = Field(gt=0)
    total_cost: Decimal = Field(gt=0)
    odometer_reading: int = Field(ge=0)
    station_name: str | None = Field(default=None, max_length=100)
    station_location: str | None = Field(default=None, max_length=200)
    receipt_number: str | None = Field(default=None, max_length=50)
    is_full_tank: bool = True
    notes: str | None = None


class FuelLogCreate(FuelLogBase):
    """Create fuel log request."""

    employee_id: UUID | None = None
    expense_claim_id: UUID | None = None


class FuelLogUpdate(BaseModel):
    """Update fuel log request."""

    log_date: date | None = None
    fuel_type: FuelType | None = None
    quantity_liters: Decimal | None = Field(default=None, gt=0)
    price_per_liter: Decimal | None = Field(default=None, gt=0)
    total_cost: Decimal | None = Field(default=None, gt=0)
    odometer_reading: int | None = Field(default=None, ge=0)
    station_name: str | None = Field(default=None, max_length=100)
    station_location: str | None = Field(default=None, max_length=200)
    receipt_number: str | None = Field(default=None, max_length=50)
    is_full_tank: bool | None = None
    employee_id: UUID | None = None
    expense_claim_id: UUID | None = None
    notes: str | None = None


class FuelLogRead(FuelLogBase):
    """Fuel log response."""

    model_config = ConfigDict(from_attributes=True)

    fuel_log_id: UUID
    organization_id: UUID
    employee_id: UUID | None = None
    expense_claim_id: UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None


class FuelLogBrief(BaseModel):
    """Brief fuel log summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    fuel_log_id: UUID
    vehicle_id: UUID
    log_date: date
    fuel_type: FuelType
    quantity_liters: Decimal
    total_cost: Decimal
    odometer_reading: int
    is_full_tank: bool


class FuelEfficiencyReport(BaseModel):
    """Fuel efficiency calculation result."""

    vehicle_id: UUID
    period_start: date
    period_end: date
    total_distance_km: int
    total_fuel_liters: Decimal
    total_cost: Decimal
    average_efficiency_km_per_liter: Decimal
    average_cost_per_km: Decimal
    fill_count: int


class FuelLogListResponse(BaseModel):
    """Paginated fuel log list response."""

    items: list[FuelLogBrief]
    total: int
    offset: int
    limit: int
