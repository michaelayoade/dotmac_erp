"""
Maintenance Record Pydantic Schemas.

Schemas for maintenance API endpoints.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import MaintenanceStatus, MaintenanceType


class MaintenanceBase(BaseModel):
    """Base maintenance schema."""

    vehicle_id: UUID
    maintenance_type: MaintenanceType
    description: str = Field(max_length=500)
    scheduled_date: date
    estimated_cost: Optional[Decimal] = Field(default=None, ge=0)
    supplier_id: Optional[UUID] = None
    notes: Optional[str] = None


class MaintenanceCreate(MaintenanceBase):
    """Create maintenance record request."""

    pass


class MaintenanceUpdate(BaseModel):
    """Update maintenance record request."""

    maintenance_type: Optional[MaintenanceType] = None
    description: Optional[str] = Field(default=None, max_length=500)
    scheduled_date: Optional[date] = None
    estimated_cost: Optional[Decimal] = Field(default=None, ge=0)
    supplier_id: Optional[UUID] = None
    notes: Optional[str] = None


class MaintenanceRead(MaintenanceBase):
    """Maintenance record response."""

    model_config = ConfigDict(from_attributes=True)

    maintenance_id: UUID
    organization_id: UUID
    status: MaintenanceStatus
    completed_date: Optional[date] = None
    odometer_at_service: Optional[int] = None
    next_service_odometer: Optional[int] = None
    next_service_date: Optional[date] = None
    actual_cost: Optional[Decimal] = None
    invoice_id: Optional[UUID] = None
    invoice_number: Optional[str] = None
    work_performed: Optional[str] = None
    parts_replaced: Optional[str] = None
    technician_name: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class MaintenanceBrief(BaseModel):
    """Brief maintenance summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    maintenance_id: UUID
    vehicle_id: UUID
    maintenance_type: MaintenanceType
    description: str
    scheduled_date: date
    status: MaintenanceStatus
    estimated_cost: Optional[Decimal] = None
    actual_cost: Optional[Decimal] = None


class MaintenanceComplete(BaseModel):
    """Request to complete maintenance."""

    completed_date: Optional[date] = None
    odometer_at_service: Optional[int] = Field(default=None, ge=0)
    actual_cost: Decimal = Field(ge=0)
    work_performed: Optional[str] = None
    parts_replaced: Optional[str] = None
    technician_name: Optional[str] = Field(default=None, max_length=100)
    invoice_number: Optional[str] = Field(default=None, max_length=50)
    next_service_odometer: Optional[int] = Field(default=None, ge=0)
    next_service_date: Optional[date] = None


class MaintenanceListResponse(BaseModel):
    """Paginated maintenance list response."""

    items: List[MaintenanceBrief]
    total: int
    offset: int
    limit: int
