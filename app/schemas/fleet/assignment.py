"""
Vehicle Assignment Pydantic Schemas.

Schemas for vehicle assignment API endpoints.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import AssignmentType


class AssignmentBase(BaseModel):
    """Base assignment schema."""

    vehicle_id: UUID
    assignment_type: AssignmentType
    start_date: date
    start_odometer: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=200)
    notes: str | None = None


class AssignmentCreate(AssignmentBase):
    """Create assignment request."""

    employee_id: UUID | None = None
    department_id: UUID | None = None


class AssignmentUpdate(BaseModel):
    """Update assignment request."""

    reason: str | None = Field(default=None, max_length=200)
    notes: str | None = None


class AssignmentRead(AssignmentBase):
    """Assignment response."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    organization_id: UUID
    employee_id: UUID | None = None
    department_id: UUID | None = None
    end_date: date | None = None
    end_odometer: int | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime | None = None


class AssignmentBrief(BaseModel):
    """Brief assignment summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    vehicle_id: UUID
    assignment_type: AssignmentType
    start_date: date
    end_date: date | None = None
    is_active: bool = True


class AssignmentWithDetails(AssignmentRead):
    """Assignment with related data."""

    model_config = ConfigDict(from_attributes=True)

    employee_name: str | None = None
    department_name: str | None = None
    vehicle_name: str | None = None
    distance_traveled: int | None = None
    duration_days: int | None = None


class AssignmentEnd(BaseModel):
    """Request to end an assignment."""

    end_date: date | None = None
    end_odometer: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=200)


class AssignmentListResponse(BaseModel):
    """Paginated assignment list response."""

    items: list[AssignmentBrief]
    total: int
    offset: int
    limit: int
