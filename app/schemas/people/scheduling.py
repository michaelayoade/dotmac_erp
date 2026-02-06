"""
Shift Scheduling Pydantic Schemas.

Pydantic schemas for Scheduling APIs including:
- Shift Patterns
- Pattern Assignments
- Shift Schedules
- Swap Requests
"""

from datetime import date, datetime, time
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Valid day codes for work_days
VALID_DAY_CODES = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}

from app.models.people.scheduling import (
    RotationType,
    ScheduleStatus,
    SwapRequestStatus,
)


# =============================================================================
# Shift Pattern Schemas
# =============================================================================


class ShiftPatternBase(BaseModel):
    """Base shift pattern schema."""

    pattern_code: str = Field(max_length=30)
    pattern_name: str = Field(max_length=100)
    description: Optional[str] = None
    rotation_type: RotationType = RotationType.DAY_ONLY
    cycle_weeks: int = Field(default=1, ge=1, le=4)
    work_days: List[str] = Field(
        default=["MON", "TUE", "WED", "THU", "FRI"],
        description="Days of the week: MON, TUE, WED, THU, FRI, SAT, SUN",
    )
    day_shift_type_id: UUID
    night_shift_type_id: Optional[UUID] = None
    is_active: bool = True

    @field_validator("work_days")
    @classmethod
    def validate_work_days(cls, v: List[str]) -> List[str]:
        """Validate that work_days contains only valid day codes."""
        if not v:
            raise ValueError("work_days cannot be empty")
        invalid_days = [day for day in v if day not in VALID_DAY_CODES]
        if invalid_days:
            raise ValueError(
                f"Invalid day codes: {invalid_days}. "
                f"Valid values are: {sorted(VALID_DAY_CODES)}"
            )
        return v


class ShiftPatternCreate(ShiftPatternBase):
    """Create shift pattern request."""

    pass


class ShiftPatternUpdate(BaseModel):
    """Update shift pattern request."""

    pattern_code: Optional[str] = Field(default=None, max_length=30)
    pattern_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    rotation_type: Optional[RotationType] = None
    cycle_weeks: Optional[int] = Field(default=None, ge=1, le=4)
    work_days: Optional[List[str]] = None
    day_shift_type_id: Optional[UUID] = None
    night_shift_type_id: Optional[UUID] = None
    is_active: Optional[bool] = None

    @field_validator("work_days")
    @classmethod
    def validate_work_days(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Validate that work_days contains only valid day codes when provided."""
        if v is None:
            return v
        if not v:
            raise ValueError("work_days cannot be empty")
        invalid_days = [day for day in v if day not in VALID_DAY_CODES]
        if invalid_days:
            raise ValueError(
                f"Invalid day codes: {invalid_days}. "
                f"Valid values are: {sorted(VALID_DAY_CODES)}"
            )
        return v


class ShiftTypeBrief(BaseModel):
    """Brief shift type info for pattern responses."""

    model_config = ConfigDict(from_attributes=True)

    shift_type_id: UUID
    shift_code: str
    shift_name: str
    start_time: time
    end_time: time


class ShiftPatternRead(ShiftPatternBase):
    """Shift pattern response."""

    model_config = ConfigDict(from_attributes=True)

    shift_pattern_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    day_shift_type: Optional[ShiftTypeBrief] = None
    night_shift_type: Optional[ShiftTypeBrief] = None


class ShiftPatternListResponse(BaseModel):
    """Paginated shift pattern list response."""

    items: List[ShiftPatternRead]
    total: int
    offset: int
    limit: int


class ShiftPatternBrief(BaseModel):
    """Brief shift pattern info."""

    model_config = ConfigDict(from_attributes=True)

    shift_pattern_id: UUID
    pattern_code: str
    pattern_name: str
    rotation_type: RotationType


# =============================================================================
# Pattern Assignment Schemas
# =============================================================================


class PatternAssignmentBase(BaseModel):
    """Base pattern assignment schema."""

    employee_id: UUID
    department_id: UUID
    shift_pattern_id: UUID
    rotation_week_offset: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Week offset for staggered rotations (0-3)",
    )
    effective_from: date
    effective_to: Optional[date] = None
    is_active: bool = True


class PatternAssignmentCreate(PatternAssignmentBase):
    """Create pattern assignment request."""

    pass


class PatternAssignmentBulkCreate(BaseModel):
    """Bulk create pattern assignments."""

    employee_ids: List[UUID]
    department_id: UUID
    shift_pattern_id: UUID
    rotation_week_offset: int = 0
    effective_from: date
    effective_to: Optional[date] = None


class PatternAssignmentUpdate(BaseModel):
    """Update pattern assignment request."""

    shift_pattern_id: Optional[UUID] = None
    rotation_week_offset: Optional[int] = Field(default=None, ge=0, le=3)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_active: Optional[bool] = None


class EmployeeBrief(BaseModel):
    """Brief employee info for assignment responses."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_code: str


class DepartmentBrief(BaseModel):
    """Brief department info for assignment responses."""

    model_config = ConfigDict(from_attributes=True)

    department_id: UUID
    department_code: str
    department_name: str


class PatternAssignmentRead(BaseModel):
    """Pattern assignment response."""

    model_config = ConfigDict(from_attributes=True)

    pattern_assignment_id: UUID
    organization_id: UUID
    employee_id: UUID
    department_id: UUID
    shift_pattern_id: UUID
    rotation_week_offset: int
    effective_from: date
    effective_to: Optional[date] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    employee: Optional[EmployeeBrief] = None
    department: Optional[DepartmentBrief] = None
    shift_pattern: Optional[ShiftPatternBrief] = None


class PatternAssignmentListResponse(BaseModel):
    """Paginated pattern assignment list response."""

    items: List[PatternAssignmentRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Shift Schedule Schemas
# =============================================================================


class ShiftScheduleBase(BaseModel):
    """Base shift schedule schema."""

    employee_id: UUID
    department_id: UUID
    shift_date: date
    shift_type_id: UUID
    notes: Optional[str] = None


class ShiftScheduleCreate(ShiftScheduleBase):
    """Create shift schedule request (for manual additions)."""

    pass


class ShiftScheduleUpdate(BaseModel):
    """Update shift schedule request."""

    shift_type_id: Optional[UUID] = None
    notes: Optional[str] = None


class ScheduleGenerateRequest(BaseModel):
    """Request to generate monthly schedules."""

    department_id: UUID
    year_month: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}$",
        description="Month to generate schedules for in YYYY-MM format",
    )


class SchedulePublishRequest(BaseModel):
    """Request to publish monthly schedules."""

    department_id: UUID
    year_month: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}$",
        description="Month to publish in YYYY-MM format",
    )


class ShiftScheduleRead(BaseModel):
    """Shift schedule response."""

    model_config = ConfigDict(from_attributes=True)

    shift_schedule_id: UUID
    organization_id: UUID
    employee_id: UUID
    department_id: UUID
    shift_date: date
    shift_type_id: UUID
    schedule_month: str
    status: ScheduleStatus
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    published_at: Optional[datetime] = None

    employee: Optional[EmployeeBrief] = None
    department: Optional[DepartmentBrief] = None
    shift_type: Optional[ShiftTypeBrief] = None


class ShiftScheduleListResponse(BaseModel):
    """Paginated shift schedule list response."""

    items: List[ShiftScheduleRead]
    total: int
    offset: int
    limit: int


class ScheduleCalendarDay(BaseModel):
    """Single day in calendar view."""

    date: date
    day_name: str
    schedules: List[ShiftScheduleRead]


class ScheduleCalendarResponse(BaseModel):
    """Calendar view of schedules for a month."""

    year_month: str
    department_id: UUID
    days: List[ScheduleCalendarDay]
    status: ScheduleStatus  # Overall status for the month


class GenerateScheduleResult(BaseModel):
    """Result of schedule generation."""

    year_month: str
    department_id: UUID
    schedules_created: int
    employees_scheduled: int
    skipped_on_leave: int


# =============================================================================
# Swap Request Schemas
# =============================================================================


class SwapRequestBase(BaseModel):
    """Base swap request schema."""

    requester_schedule_id: UUID
    target_schedule_id: UUID
    reason: Optional[str] = None


class SwapRequestCreate(SwapRequestBase):
    """Create swap request."""

    pass


class SwapRequestReview(BaseModel):
    """Manager review of swap request."""

    notes: Optional[str] = None


class SwapRequestRead(BaseModel):
    """Swap request response."""

    model_config = ConfigDict(from_attributes=True)

    swap_request_id: UUID
    organization_id: UUID
    requester_schedule_id: UUID
    target_schedule_id: UUID
    requester_id: UUID
    target_employee_id: UUID
    status: SwapRequestStatus
    reason: Optional[str] = None
    target_accepted_at: Optional[datetime] = None
    reviewed_by_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    requester: Optional[EmployeeBrief] = None
    target_employee: Optional[EmployeeBrief] = None
    requester_schedule: Optional[ShiftScheduleRead] = None
    target_schedule: Optional[ShiftScheduleRead] = None


class SwapRequestListResponse(BaseModel):
    """Paginated swap request list response."""

    items: List[SwapRequestRead]
    total: int
    offset: int
    limit: int
