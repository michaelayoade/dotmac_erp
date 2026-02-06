"""
Time Entry Pydantic Schemas.

Schemas for PM Time Entry API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.pm import BillingStatus


# =============================================================================
# Time Entry Schemas
# =============================================================================


class TimeEntryBase(BaseModel):
    """Base time entry schema."""

    project_id: UUID
    task_id: Optional[UUID] = None
    employee_id: UUID
    entry_date: date
    hours: Decimal = Field(gt=0, le=24)
    description: Optional[str] = None
    is_billable: bool = True


class TimeEntryCreate(TimeEntryBase):
    """Create time entry request."""

    pass


class TimeEntryUpdate(BaseModel):
    """Update time entry request."""

    task_id: Optional[UUID] = None
    entry_date: Optional[date] = None
    hours: Optional[Decimal] = Field(default=None, gt=0, le=24)
    description: Optional[str] = None
    is_billable: Optional[bool] = None


class TimeEntryRead(TimeEntryBase):
    """Time entry response."""

    model_config = ConfigDict(from_attributes=True)

    entry_id: UUID
    organization_id: UUID
    billing_status: BillingStatus = BillingStatus.NOT_BILLED
    created_at: datetime
    updated_at: Optional[datetime] = None


class TimeEntryWithDetails(TimeEntryRead):
    """Time entry with related data."""

    model_config = ConfigDict(from_attributes=True)

    project_name: Optional[str] = None
    task_name: Optional[str] = None
    employee_name: Optional[str] = None


class TimeEntryListResponse(BaseModel):
    """Paginated time entry list response."""

    items: List[TimeEntryWithDetails]
    total: int
    offset: int
    limit: int


# =============================================================================
# Timesheet Schemas
# =============================================================================


class TimesheetDay(BaseModel):
    """Time entries for a single day."""

    date: date
    entries: List[TimeEntryWithDetails]
    total_hours: Decimal = Decimal("0.00")


class TimesheetWeek(BaseModel):
    """Weekly timesheet for an employee."""

    employee_id: UUID
    employee_name: str
    week_start: date
    week_end: date
    days: List[TimesheetDay]
    total_hours: Decimal = Decimal("0.00")
    billable_hours: Decimal = Decimal("0.00")


# =============================================================================
# Project Time Summary Schemas
# =============================================================================


class ProjectTimeSummary(BaseModel):
    """Time summary for a project."""

    project_id: UUID
    project_name: str
    total_hours: Decimal = Decimal("0.00")
    billable_hours: Decimal = Decimal("0.00")
    non_billable_hours: Decimal = Decimal("0.00")
    billed_hours: Decimal = Decimal("0.00")
    unbilled_hours: Decimal = Decimal("0.00")
    hours_by_employee: Dict[str, Decimal] = {}
    hours_by_task: Dict[str, Decimal] = {}


class EmployeeTimeSummary(BaseModel):
    """Time summary for an employee across projects."""

    employee_id: UUID
    employee_name: str
    period_start: date
    period_end: date
    total_hours: Decimal = Decimal("0.00")
    billable_hours: Decimal = Decimal("0.00")
    hours_by_project: Dict[str, Decimal] = {}
