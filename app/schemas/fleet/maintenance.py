"""
Maintenance Record Pydantic Schemas.

Schemas for maintenance API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import MaintenanceStatus, MaintenanceType


class MaintenanceBase(BaseModel):
    """Base maintenance schema."""

    vehicle_id: UUID
    maintenance_type: MaintenanceType
    description: str = Field(max_length=500)
    scheduled_date: date
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    supplier_id: UUID | None = None
    notes: str | None = None


class MaintenanceCreate(MaintenanceBase):
    """Create maintenance record request."""

    pass


class MaintenanceUpdate(BaseModel):
    """Update maintenance record request."""

    maintenance_type: MaintenanceType | None = None
    description: str | None = Field(default=None, max_length=500)
    scheduled_date: date | None = None
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    supplier_id: UUID | None = None
    notes: str | None = None


class MaintenanceRead(MaintenanceBase):
    """Maintenance record response."""

    model_config = ConfigDict(from_attributes=True)

    maintenance_id: UUID
    organization_id: UUID
    status: MaintenanceStatus
    completed_date: date | None = None
    odometer_at_service: int | None = None
    next_service_odometer: int | None = None
    next_service_date: date | None = None
    actual_cost: Decimal | None = None
    invoice_id: UUID | None = None
    invoice_number: str | None = None
    work_performed: str | None = None
    parts_replaced: str | None = None
    technician_name: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class MaintenanceBrief(BaseModel):
    """Brief maintenance summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    maintenance_id: UUID
    vehicle_id: UUID
    maintenance_type: MaintenanceType
    description: str
    scheduled_date: date
    status: MaintenanceStatus
    estimated_cost: Decimal | None = None
    actual_cost: Decimal | None = None


class MaintenanceComplete(BaseModel):
    """Request to complete maintenance."""

    completed_date: date | None = None
    odometer_at_service: int | None = Field(default=None, ge=0)
    actual_cost: Decimal = Field(ge=0)
    work_performed: str | None = None
    parts_replaced: str | None = None
    technician_name: str | None = Field(default=None, max_length=100)
    invoice_number: str | None = Field(default=None, max_length=50)
    next_service_odometer: int | None = Field(default=None, ge=0)
    next_service_date: date | None = None


class MaintenanceListResponse(BaseModel):
    """Paginated maintenance list response."""

    items: list[MaintenanceBrief]
    total: int
    offset: int
    limit: int
