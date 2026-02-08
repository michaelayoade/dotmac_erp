"""
Time Entry Pydantic Schemas.

Schemas for PM Time Entry API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.pm import BillingStatus

# =============================================================================
# Time Entry Schemas
# =============================================================================


class TimeEntryBase(BaseModel):
    """Base time entry schema."""

    project_id: UUID
    task_id: UUID | None = None
    employee_id: UUID
    entry_date: date
    hours: Decimal = Field(gt=0, le=24)
    description: str | None = None
    is_billable: bool = True


class TimeEntryCreate(TimeEntryBase):
    """Create time entry request."""

    pass


class TimeEntryUpdate(BaseModel):
    """Update time entry request."""

    task_id: UUID | None = None
    entry_date: date | None = None
    hours: Decimal | None = Field(default=None, gt=0, le=24)
    description: str | None = None
    is_billable: bool | None = None


class TimeEntryRead(TimeEntryBase):
    """Time entry response."""

    model_config = ConfigDict(from_attributes=True)

    entry_id: UUID
    organization_id: UUID
    billing_status: BillingStatus = BillingStatus.NOT_BILLED
    created_at: datetime
    updated_at: datetime | None = None


class TimeEntryWithDetails(TimeEntryRead):
    """Time entry with related data."""

    model_config = ConfigDict(from_attributes=True)

    project_name: str | None = None
    task_name: str | None = None
    employee_name: str | None = None


class TimeEntryListResponse(BaseModel):
    """Paginated time entry list response."""

    items: list[TimeEntryWithDetails]
    total: int
    offset: int
    limit: int


# =============================================================================
# Timesheet Schemas
# =============================================================================


class TimesheetDay(BaseModel):
    """Time entries for a single day."""

    date: date
    entries: list[TimeEntryWithDetails]
    total_hours: Decimal = Decimal("0.00")


class TimesheetWeek(BaseModel):
    """Weekly timesheet for an employee."""

    employee_id: UUID
    employee_name: str
    week_start: date
    week_end: date
    days: list[TimesheetDay]
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
    hours_by_employee: dict[str, Decimal] = {}
    hours_by_task: dict[str, Decimal] = {}


class EmployeeTimeSummary(BaseModel):
    """Time summary for an employee across projects."""

    employee_id: UUID
    employee_name: str
    period_start: date
    period_end: date
    total_hours: Decimal = Decimal("0.00")
    billable_hours: Decimal = Decimal("0.00")
    hours_by_project: dict[str, Decimal] = {}
