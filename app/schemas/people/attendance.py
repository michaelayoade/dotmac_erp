"""
Attendance Management Pydantic Schemas.

Pydantic schemas for Attendance APIs including:
- Shift Type
- Attendance Records
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.attendance import AttendanceStatus, AttendanceRequestStatus


# =============================================================================
# Shift Type Schemas
# =============================================================================


class ShiftTypeBase(BaseModel):
    """Base shift type schema."""

    shift_code: str = Field(max_length=30)
    shift_name: str = Field(max_length=100)
    description: Optional[str] = None
    start_time: time
    end_time: time
    working_hours: Decimal
    late_entry_grace_period: int = 0
    early_exit_grace_period: int = 0
    enable_half_day: bool = True
    half_day_threshold_hours: Optional[Decimal] = None
    enable_overtime: bool = False
    overtime_threshold_hours: Optional[Decimal] = None
    break_duration_minutes: int = 60
    is_active: bool = True


class ShiftTypeCreate(ShiftTypeBase):
    """Create shift type request."""

    pass


class ShiftTypeUpdate(BaseModel):
    """Update shift type request."""

    shift_code: Optional[str] = Field(default=None, max_length=30)
    shift_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    working_hours: Optional[Decimal] = None
    late_entry_grace_period: Optional[int] = None
    early_exit_grace_period: Optional[int] = None
    enable_half_day: Optional[bool] = None
    half_day_threshold_hours: Optional[Decimal] = None
    enable_overtime: Optional[bool] = None
    overtime_threshold_hours: Optional[Decimal] = None
    break_duration_minutes: Optional[int] = None
    is_active: Optional[bool] = None


class ShiftTypeRead(ShiftTypeBase):
    """Shift type response."""

    model_config = ConfigDict(from_attributes=True)

    shift_type_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class ShiftTypeListResponse(BaseModel):
    """Paginated shift type list response."""

    items: List[ShiftTypeRead]
    total: int
    offset: int
    limit: int


class ShiftTypeBrief(BaseModel):
    """Brief shift type info."""

    model_config = ConfigDict(from_attributes=True)

    shift_type_id: UUID
    shift_code: str
    shift_name: str
    start_time: time
    end_time: time


# =============================================================================
# Shift Assignment Schemas
# =============================================================================


class ShiftAssignmentBase(BaseModel):
    """Base shift assignment schema."""

    employee_id: UUID
    shift_type_id: UUID
    start_date: date
    end_date: Optional[date] = None
    is_active: bool = True


class ShiftAssignmentCreate(ShiftAssignmentBase):
    """Create shift assignment request."""

    pass


class ShiftAssignmentUpdate(BaseModel):
    """Update shift assignment request."""

    shift_type_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None


class ShiftAssignmentRead(BaseModel):
    """Shift assignment response."""

    model_config = ConfigDict(from_attributes=True)

    shift_assignment_id: UUID
    organization_id: UUID
    employee_id: UUID
    shift_type_id: UUID
    start_date: date
    end_date: Optional[date] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None


class ShiftAssignmentListResponse(BaseModel):
    """Paginated shift assignment list response."""

    items: List[ShiftAssignmentRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Attendance Schemas
# =============================================================================


class AttendanceBase(BaseModel):
    """Base attendance schema."""

    employee_id: UUID
    attendance_date: date
    shift_type_id: Optional[UUID] = None
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    status: AttendanceStatus
    remarks: Optional[str] = None
    marked_by: str = "MANUAL"


class AttendanceCreate(AttendanceBase):
    """Create attendance request."""

    leave_application_id: Optional[UUID] = None


class AttendanceUpdate(BaseModel):
    """Update attendance request."""

    shift_type_id: Optional[UUID] = None
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    status: Optional[AttendanceStatus] = None
    remarks: Optional[str] = None
    leave_application_id: Optional[UUID] = None


class EmployeeBrief(BaseModel):
    """Brief employee info for attendance responses."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_code: str


class AttendanceRead(BaseModel):
    """Attendance response."""

    model_config = ConfigDict(from_attributes=True)

    attendance_id: UUID
    organization_id: UUID
    employee_id: UUID
    attendance_date: date
    shift_type_id: Optional[UUID] = None
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    working_hours: Optional[Decimal] = None
    overtime_hours: Decimal
    status: AttendanceStatus
    late_entry: bool
    late_entry_minutes: int
    early_exit: bool
    early_exit_minutes: int
    leave_application_id: Optional[UUID] = None
    remarks: Optional[str] = None
    marked_by: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    shift_type: Optional[ShiftTypeBrief] = None
    employee: Optional[EmployeeBrief] = None


class AttendanceListResponse(BaseModel):
    """Paginated attendance list response."""

    items: List[AttendanceRead]
    total: int
    offset: int
    limit: int


class BulkAttendanceCreate(BaseModel):
    """Bulk create attendance records."""

    employee_ids: List[UUID]
    attendance_date: date
    status: AttendanceStatus
    shift_type_id: Optional[UUID] = None
    remarks: Optional[str] = None


class CheckInRequest(BaseModel):
    """Employee check-in request."""

    employee_id: UUID
    check_in_time: Optional[datetime] = None
    shift_type_id: Optional[UUID] = None
    notes: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class CheckOutRequest(BaseModel):
    """Employee check-out request."""

    employee_id: UUID
    check_out_time: Optional[datetime] = None
    notes: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class AttendanceRecordCheckIn(BaseModel):
    """Check-in request for an existing attendance record."""

    check_in_time: Optional[datetime] = None
    notes: Optional[str] = None


class AttendanceRecordCheckOut(BaseModel):
    """Check-out request for an existing attendance record."""

    check_out_time: Optional[datetime] = None
    notes: Optional[str] = None


class AttendanceRecordCheckIn(BaseModel):
    """Attendance record check-in request."""

    check_in_time: Optional[datetime] = None
    notes: Optional[str] = None


class AttendanceRecordCheckOut(BaseModel):
    """Attendance record check-out request."""

    check_out_time: Optional[datetime] = None
    notes: Optional[str] = None


class AttendanceSummary(BaseModel):
    """Attendance summary for a period."""

    employee_id: UUID
    total_present: int
    total_absent: int
    total_half_day: int
    total_leave: int
    total_late: int
    total_early_exit: int
    total_overtime_hours: Decimal


class DailyAttendanceSummary(BaseModel):
    """Daily attendance summary for dashboard."""

    attendance_date: date
    total_employees: int
    present: int
    absent: int
    on_leave: int
    half_day: int
    work_from_home: int
    not_marked: int


class AttendanceReport(BaseModel):
    """Attendance report for a date range."""

    start_date: date
    end_date: date
    employee_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    summary: AttendanceSummary
    records: List[AttendanceRead]


# =============================================================================
# Attendance Request Schemas
# =============================================================================


class AttendanceRequestBase(BaseModel):
    """Base attendance request schema."""

    employee_id: UUID
    from_date: date
    to_date: date
    half_day: bool = False
    half_day_date: Optional[date] = None
    reason: Optional[str] = None
    explanation: Optional[str] = None


class AttendanceRequestCreate(AttendanceRequestBase):
    """Create attendance request."""

    pass


class AttendanceRequestUpdate(BaseModel):
    """Update attendance request."""

    from_date: Optional[date] = None
    to_date: Optional[date] = None
    half_day: Optional[bool] = None
    half_day_date: Optional[date] = None
    reason: Optional[str] = None
    explanation: Optional[str] = None
    status: Optional[AttendanceRequestStatus] = None


class AttendanceRequestRead(BaseModel):
    """Attendance request response."""

    model_config = ConfigDict(from_attributes=True)

    attendance_request_id: UUID
    organization_id: UUID
    employee_id: UUID
    from_date: date
    to_date: date
    half_day: bool
    half_day_date: Optional[date] = None
    reason: Optional[str] = None
    explanation: Optional[str] = None
    status: AttendanceRequestStatus
    created_at: datetime
    updated_at: Optional[datetime] = None


class AttendanceRequestListResponse(BaseModel):
    """Paginated attendance request list response."""

    items: List[AttendanceRequestRead]
    total: int
    offset: int
    limit: int


class AttendanceRequestBulkAction(BaseModel):
    """Bulk attendance request action payload."""

    request_ids: List[UUID]
