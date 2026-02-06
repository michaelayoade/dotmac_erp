"""
Resource Allocation Pydantic Schemas.

Schemas for PM Resource Allocation API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Resource Allocation Schemas
# =============================================================================


class ResourceAllocationBase(BaseModel):
    """Base resource allocation schema."""

    project_id: UUID
    employee_id: UUID
    role_on_project: Optional[str] = Field(default=None, max_length=100)
    allocation_percent: Decimal = Field(ge=0, le=100)
    start_date: date
    end_date: Optional[date] = None
    cost_rate_per_hour: Optional[Decimal] = None
    billing_rate_per_hour: Optional[Decimal] = None


class ResourceAllocationCreate(ResourceAllocationBase):
    """Create resource allocation request."""

    pass


class ResourceAllocationUpdate(BaseModel):
    """Update resource allocation request."""

    role_on_project: Optional[str] = Field(default=None, max_length=100)
    allocation_percent: Optional[Decimal] = Field(default=None, ge=0, le=100)
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    cost_rate_per_hour: Optional[Decimal] = None
    billing_rate_per_hour: Optional[Decimal] = None


class ResourceAllocationRead(ResourceAllocationBase):
    """Resource allocation response."""

    model_config = ConfigDict(from_attributes=True)

    allocation_id: UUID
    organization_id: UUID
    is_active: bool = True
    created_at: datetime
    updated_at: Optional[datetime] = None


class ResourceAllocationWithDetails(ResourceAllocationRead):
    """Resource allocation with related data."""

    model_config = ConfigDict(from_attributes=True)

    project_name: Optional[str] = None
    employee_name: Optional[str] = None
    is_current: bool = False


class ResourceAllocationListResponse(BaseModel):
    """Paginated resource allocation list response."""

    items: List[ResourceAllocationWithDetails]
    total: int
    offset: int
    limit: int


# =============================================================================
# Team and Utilization Schemas
# =============================================================================


class TeamMemberSummary(BaseModel):
    """Summary of a team member on a project."""

    employee_id: UUID
    employee_name: str
    role_on_project: Optional[str] = None
    allocation_percent: Decimal
    start_date: date
    end_date: Optional[date] = None
    is_active: bool = True
    total_hours_logged: Decimal = Decimal("0.00")


class ProjectTeamResponse(BaseModel):
    """Project team list response."""

    project_id: UUID
    project_name: str
    team_members: List[TeamMemberSummary]
    total_allocation_percent: Decimal


class UtilizationSummary(BaseModel):
    """Employee utilization summary."""

    employee_id: UUID
    employee_name: str
    period_start: date
    period_end: date
    total_allocation_percent: Decimal
    available_percent: Decimal
    project_allocations: List["ProjectAllocationSummary"]


class ProjectAllocationSummary(BaseModel):
    """Summary of allocation to a specific project."""

    project_id: UUID
    project_name: str
    allocation_percent: Decimal
    role_on_project: Optional[str] = None


# =============================================================================
# End Allocation Schema
# =============================================================================


class EndAllocationRequest(BaseModel):
    """Request to end a resource allocation."""

    end_date: Optional[date] = None
