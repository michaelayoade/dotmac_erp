"""
Leave Management Pydantic Schemas.

Pydantic schemas for Leave APIs including:
- Leave Type
- Holiday List / Holiday
- Leave Allocation
- Leave Application
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.leave import LeaveTypePolicy, LeaveApplicationStatus


# =============================================================================
# Leave Type Schemas
# =============================================================================


class LeaveTypeBase(BaseModel):
    """Base leave type schema."""

    leave_type_code: str = Field(max_length=30)
    leave_type_name: str = Field(max_length=100)
    description: Optional[str] = None
    allocation_policy: LeaveTypePolicy = LeaveTypePolicy.ANNUAL
    max_days_per_year: Optional[Decimal] = None
    max_continuous_days: Optional[int] = None
    allow_carry_forward: bool = False
    max_carry_forward_days: Optional[Decimal] = None
    carry_forward_expiry_months: Optional[int] = None
    allow_encashment: bool = False
    encashment_threshold_days: Optional[Decimal] = None
    is_lwp: bool = False
    is_compensatory: bool = False
    include_holidays: bool = False
    applicable_after_days: int = 0
    is_optional: bool = False
    max_optional_leaves: Optional[int] = None
    is_active: bool = True


class LeaveTypeCreate(LeaveTypeBase):
    """Create leave type request."""

    pass


class LeaveTypeUpdate(BaseModel):
    """Update leave type request."""

    leave_type_code: Optional[str] = Field(default=None, max_length=30)
    leave_type_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    allocation_policy: Optional[LeaveTypePolicy] = None
    max_days_per_year: Optional[Decimal] = None
    max_continuous_days: Optional[int] = None
    allow_carry_forward: Optional[bool] = None
    max_carry_forward_days: Optional[Decimal] = None
    carry_forward_expiry_months: Optional[int] = None
    allow_encashment: Optional[bool] = None
    encashment_threshold_days: Optional[Decimal] = None
    is_lwp: Optional[bool] = None
    is_compensatory: Optional[bool] = None
    include_holidays: Optional[bool] = None
    applicable_after_days: Optional[int] = None
    is_optional: Optional[bool] = None
    max_optional_leaves: Optional[int] = None
    is_active: Optional[bool] = None


class LeaveTypeRead(LeaveTypeBase):
    """Leave type response."""

    model_config = ConfigDict(from_attributes=True)

    leave_type_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class LeaveTypeListResponse(BaseModel):
    """Paginated leave type list response."""

    items: List[LeaveTypeRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Holiday List Schemas
# =============================================================================


class HolidayBase(BaseModel):
    """Base holiday schema."""

    holiday_date: date
    holiday_name: str = Field(max_length=100)
    description: Optional[str] = Field(default=None, max_length=255)
    is_public_holiday: bool = True
    is_optional: bool = False


class HolidayCreate(HolidayBase):
    """Create holiday request."""

    pass


class HolidayRead(HolidayBase):
    """Holiday response."""

    model_config = ConfigDict(from_attributes=True)

    holiday_id: UUID
    holiday_list_id: UUID


class HolidayListBase(BaseModel):
    """Base holiday list schema."""

    list_code: str = Field(max_length=30)
    list_name: str = Field(max_length=100)
    description: Optional[str] = None
    year: int
    from_date: date
    to_date: date
    weekly_off: str = "Saturday,Sunday"
    is_default: bool = False
    is_active: bool = True


class HolidayListCreate(HolidayListBase):
    """Create holiday list request."""

    holidays: List[HolidayCreate] = []


class HolidayListUpdate(BaseModel):
    """Update holiday list request."""

    list_code: Optional[str] = Field(default=None, max_length=30)
    list_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    year: Optional[int] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    weekly_off: Optional[str] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class HolidayListRead(HolidayListBase):
    """Holiday list response."""

    model_config = ConfigDict(from_attributes=True)

    holiday_list_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None

    holidays: List[HolidayRead] = []


class HolidayListSummary(BaseModel):
    """Holiday list summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    holiday_list_id: UUID
    list_code: str
    list_name: str
    year: int
    is_default: bool
    total_holidays: int = 0


# =============================================================================
# Leave Allocation Schemas
# =============================================================================


class LeaveAllocationBase(BaseModel):
    """Base leave allocation schema."""

    employee_id: UUID
    leave_type_id: UUID
    from_date: date
    to_date: date
    new_leaves_allocated: Decimal = Decimal("0")
    carry_forward_leaves: Decimal = Decimal("0")
    notes: Optional[str] = None
    is_active: bool = True


class LeaveAllocationCreate(LeaveAllocationBase):
    """Create leave allocation request."""

    pass


class LeaveAllocationUpdate(BaseModel):
    """Update leave allocation request."""

    from_date: Optional[date] = None
    to_date: Optional[date] = None
    new_leaves_allocated: Optional[Decimal] = None
    carry_forward_leaves: Optional[Decimal] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class LeaveTypeBrief(BaseModel):
    """Brief leave type info."""

    model_config = ConfigDict(from_attributes=True)

    leave_type_id: UUID
    leave_type_code: str
    leave_type_name: str
    is_lwp: bool


class LeaveAllocationRead(BaseModel):
    """Leave allocation response."""

    model_config = ConfigDict(from_attributes=True)

    allocation_id: UUID
    organization_id: UUID
    employee_id: UUID
    leave_type_id: UUID
    from_date: date
    to_date: date
    new_leaves_allocated: Decimal
    carry_forward_leaves: Decimal
    total_leaves_allocated: Decimal
    leaves_used: Decimal
    leaves_encashed: Decimal
    leaves_expired: Decimal
    notes: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    leave_type: Optional[LeaveTypeBrief] = None


class LeaveAllocationListResponse(BaseModel):
    """Paginated leave allocation list response."""

    items: List[LeaveAllocationRead]
    total: int
    offset: int
    limit: int


class BulkLeaveAllocationCreate(BaseModel):
    """Bulk create leave allocations."""

    employee_ids: List[UUID]
    leave_type_id: UUID
    from_date: date
    to_date: date
    new_leaves_allocated: Decimal = Decimal("0")
    carry_forward_leaves: Decimal = Decimal("0")
    notes: Optional[str] = None


class BulkLeaveAllocationResult(BaseModel):
    """Bulk leave allocation result."""

    success_count: int
    failed_count: int
    errors: List[dict] = []


class LeaveBalanceSummary(BaseModel):
    """Leave balance summary for an employee."""

    leave_type_id: UUID
    leave_type_code: str
    leave_type_name: str
    allocated: Decimal
    used: Decimal
    remaining: Decimal


# =============================================================================
# Leave Application Schemas
# =============================================================================


class LeaveApplicationBase(BaseModel):
    """Base leave application schema."""

    employee_id: UUID
    leave_type_id: UUID
    from_date: date
    to_date: date
    half_day: bool = False
    half_day_date: Optional[date] = None
    total_leave_days: Decimal
    reason: Optional[str] = None
    contact_during_leave: Optional[str] = Field(default=None, max_length=100)
    address_during_leave: Optional[str] = None


class LeaveApplicationCreate(LeaveApplicationBase):
    """Create leave application request."""

    leave_approver_id: Optional[UUID] = None


class LeaveApplicationUpdate(BaseModel):
    """Update leave application request."""

    from_date: Optional[date] = None
    to_date: Optional[date] = None
    half_day: Optional[bool] = None
    half_day_date: Optional[date] = None
    total_leave_days: Optional[Decimal] = None
    reason: Optional[str] = None
    contact_during_leave: Optional[str] = Field(default=None, max_length=100)
    address_during_leave: Optional[str] = None
    leave_approver_id: Optional[UUID] = None


class EmployeeBrief(BaseModel):
    """Brief employee info for leave application responses."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_code: str


class LeaveApplicationRead(BaseModel):
    """Leave application response."""

    model_config = ConfigDict(from_attributes=True)

    application_id: UUID
    organization_id: UUID
    application_number: str
    employee_id: UUID
    leave_type_id: UUID
    from_date: date
    to_date: date
    half_day: bool
    half_day_date: Optional[date] = None
    total_leave_days: Decimal
    reason: Optional[str] = None
    contact_during_leave: Optional[str] = None
    address_during_leave: Optional[str] = None
    status: LeaveApplicationStatus
    leave_approver_id: Optional[UUID] = None
    approved_by_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    is_posted_to_payroll: bool
    salary_slip_id: Optional[UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    leave_type: Optional[LeaveTypeBrief] = None
    employee: Optional[EmployeeBrief] = None


class LeaveApplicationListResponse(BaseModel):
    """Paginated leave application list response."""

    items: List[LeaveApplicationRead]
    total: int
    offset: int
    limit: int


class LeaveApplicationBulkAction(BaseModel):
    """Bulk approve/reject leave applications."""

    application_ids: List[UUID]
    reason: Optional[str] = None


class LeaveApprovalRequest(BaseModel):
    """Approve/reject leave application request."""

    action: str = Field(description="APPROVE or REJECT")
    rejection_reason: Optional[str] = None


class LeaveSubmitRequest(BaseModel):
    """Submit leave application for approval."""

    pass


class LeaveCancelRequest(BaseModel):
    """Cancel leave application request."""

    reason: Optional[str] = None


class LeaveStats(BaseModel):
    """Leave statistics for dashboard."""

    total_applications: int
    pending_approval: int
    approved: int
    on_leave_today: int
