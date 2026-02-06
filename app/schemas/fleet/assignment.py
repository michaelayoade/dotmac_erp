"""
Vehicle Assignment Pydantic Schemas.

Schemas for vehicle assignment API endpoints.
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import AssignmentType


class AssignmentBase(BaseModel):
    """Base assignment schema."""

    vehicle_id: UUID
    assignment_type: AssignmentType
    start_date: date
    start_odometer: Optional[int] = Field(default=None, ge=0)
    reason: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None


class AssignmentCreate(AssignmentBase):
    """Create assignment request."""

    employee_id: Optional[UUID] = None
    department_id: Optional[UUID] = None


class AssignmentUpdate(BaseModel):
    """Update assignment request."""

    reason: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None


class AssignmentRead(AssignmentBase):
    """Assignment response."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    organization_id: UUID
    employee_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    end_date: Optional[date] = None
    end_odometer: Optional[int] = None
    is_active: bool = True
    created_at: datetime
    updated_at: Optional[datetime] = None


class AssignmentBrief(BaseModel):
    """Brief assignment summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    vehicle_id: UUID
    assignment_type: AssignmentType
    start_date: date
    end_date: Optional[date] = None
    is_active: bool = True


class AssignmentWithDetails(AssignmentRead):
    """Assignment with related data."""

    model_config = ConfigDict(from_attributes=True)

    employee_name: Optional[str] = None
    department_name: Optional[str] = None
    vehicle_name: Optional[str] = None
    distance_traveled: Optional[int] = None
    duration_days: Optional[int] = None


class AssignmentEnd(BaseModel):
    """Request to end an assignment."""

    end_date: Optional[date] = None
    end_odometer: Optional[int] = Field(default=None, ge=0)
    reason: Optional[str] = Field(default=None, max_length=200)


class AssignmentListResponse(BaseModel):
    """Paginated assignment list response."""

    items: List[AssignmentBrief]
    total: int
    offset: int
    limit: int
