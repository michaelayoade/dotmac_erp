"""
Attendance Management API Router.

Thin API wrapper for Attendance Management endpoints. All business logic is in services.
"""
from datetime import date, datetime
from typing import Optional
import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.attendance import AttendanceRequestStatus
from app.models.people.attendance.attendance import AttendanceStatus
from app.schemas.people.attendance import (
    # Shift Type
    ShiftTypeCreate,
    ShiftTypeUpdate,
    ShiftTypeRead,
    ShiftTypeListResponse,
    # Shift Assignments
    ShiftAssignmentCreate,
    ShiftAssignmentUpdate,
    ShiftAssignmentRead,
    ShiftAssignmentListResponse,
    # Attendance
    AttendanceCreate,
    AttendanceUpdate,
    AttendanceRead,
    AttendanceListResponse,
    CheckInRequest,
    CheckOutRequest,
    AttendanceRecordCheckIn,
    AttendanceRecordCheckOut,
    BulkAttendanceCreate,
    AttendanceRequestCreate,
    AttendanceRequestUpdate,
    AttendanceRequestRead,
    AttendanceRequestListResponse,
    AttendanceRequestBulkAction,
)
from app.services.people.attendance import AttendanceService
from app.services.common import PaginationParams

router = APIRouter(
    prefix="/attendance",
    tags=["attendance"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def csv_response(rows: list[list[str]], filename: str) -> Response:
    """Return a CSV response for export endpoints."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    content = buffer.getvalue()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =============================================================================
# Shift Types
# =============================================================================


@router.get("/shift-types", response_model=ShiftTypeListResponse)
def list_shift_types(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List shift types."""
    svc = AttendanceService(db)
    result = svc.list_shift_types(
        org_id=organization_id,
        search=search,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ShiftTypeListResponse(
        items=[ShiftTypeRead.model_validate(st) for st in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/shift-types", response_model=ShiftTypeRead, status_code=status.HTTP_201_CREATED)
def create_shift_type(
    payload: ShiftTypeCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a shift type."""
    svc = AttendanceService(db)
    shift_type = svc.create_shift_type(
        org_id=organization_id,
        shift_code=payload.shift_code,
        shift_name=payload.shift_name,
        description=payload.description,
        start_time=payload.start_time,
        end_time=payload.end_time,
        working_hours=payload.working_hours,
        late_entry_grace_period=payload.late_entry_grace_period,
        early_exit_grace_period=payload.early_exit_grace_period,
        enable_half_day=payload.enable_half_day,
        half_day_threshold_hours=payload.half_day_threshold_hours,
        enable_overtime=payload.enable_overtime,
        overtime_threshold_hours=payload.overtime_threshold_hours,
        break_duration_minutes=payload.break_duration_minutes,
        is_active=payload.is_active,
    )
    db.commit()
    return ShiftTypeRead.model_validate(shift_type)


@router.get("/shift-types/{shift_type_id}", response_model=ShiftTypeRead)
def get_shift_type(
    shift_type_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a shift type by ID."""
    svc = AttendanceService(db)
    return ShiftTypeRead.model_validate(svc.get_shift_type(organization_id, shift_type_id))


@router.patch("/shift-types/{shift_type_id}", response_model=ShiftTypeRead)
def update_shift_type(
    shift_type_id: UUID,
    payload: ShiftTypeUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a shift type."""
    svc = AttendanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    shift_type = svc.update_shift_type(organization_id, shift_type_id, **update_data)
    db.commit()
    return ShiftTypeRead.model_validate(shift_type)


@router.delete("/shift-types/{shift_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shift_type(
    shift_type_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a shift type."""
    svc = AttendanceService(db)
    svc.delete_shift_type(organization_id, shift_type_id)
    db.commit()


# =============================================================================
# Shift Assignments
# =============================================================================


@router.get("/shift-assignments", response_model=ShiftAssignmentListResponse)
def list_shift_assignments(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    shift_type_id: Optional[UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List shift assignments."""
    svc = AttendanceService(db)
    result = svc.list_shift_assignments(
        org_id=organization_id,
        employee_id=employee_id,
        shift_type_id=shift_type_id,
        start_date=start_date,
        end_date=end_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ShiftAssignmentListResponse(
        items=[ShiftAssignmentRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/shift-assignments",
    response_model=ShiftAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_shift_assignment(
    payload: ShiftAssignmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a shift assignment."""
    svc = AttendanceService(db)
    assignment = svc.create_shift_assignment(
        org_id=organization_id,
        employee_id=payload.employee_id,
        shift_type_id=payload.shift_type_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_active=payload.is_active,
    )
    db.commit()
    return ShiftAssignmentRead.model_validate(assignment)


@router.get("/shift-assignments/{shift_assignment_id}", response_model=ShiftAssignmentRead)
def get_shift_assignment(
    shift_assignment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a shift assignment by ID."""
    svc = AttendanceService(db)
    return ShiftAssignmentRead.model_validate(
        svc.get_shift_assignment(organization_id, shift_assignment_id)
    )


@router.patch(
    "/shift-assignments/{shift_assignment_id}",
    response_model=ShiftAssignmentRead,
)
def update_shift_assignment(
    shift_assignment_id: UUID,
    payload: ShiftAssignmentUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a shift assignment."""
    svc = AttendanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    assignment = svc.update_shift_assignment(
        organization_id,
        shift_assignment_id,
        **update_data,
    )
    db.commit()
    return ShiftAssignmentRead.model_validate(assignment)


@router.delete("/shift-assignments/{shift_assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shift_assignment(
    shift_assignment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Deactivate a shift assignment."""
    svc = AttendanceService(db)
    svc.delete_shift_assignment(organization_id, shift_assignment_id)
    db.commit()


# =============================================================================
# Attendance Records
# =============================================================================


@router.get("/records", response_model=AttendanceListResponse)
def list_attendance(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List attendance records."""
    svc = AttendanceService(db)
    status_value = None
    if status:
        try:
            status_value = AttendanceStatus(status)
        except ValueError:
            status_value = None
    result = svc.list_attendance(
        org_id=organization_id,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        status=status_value,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return AttendanceListResponse(
        items=[AttendanceRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/records", response_model=AttendanceRead, status_code=status.HTTP_201_CREATED)
def create_attendance(
    payload: AttendanceCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create an attendance record."""
    svc = AttendanceService(db)
    attendance = svc.create_attendance(
        org_id=organization_id,
        employee_id=payload.employee_id,
        attendance_date=payload.attendance_date,
        status=payload.status,
        shift_type_id=payload.shift_type_id,
        check_in=payload.check_in,
        check_out=payload.check_out,
        remarks=payload.remarks,
        marked_by=payload.marked_by,
        leave_application_id=payload.leave_application_id,
    )
    db.commit()
    return AttendanceRead.model_validate(attendance)


@router.get("/records/{attendance_id}", response_model=AttendanceRead)
def get_attendance(
    attendance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get an attendance record by ID."""
    svc = AttendanceService(db)
    return AttendanceRead.model_validate(svc.get_attendance(organization_id, attendance_id))


@router.patch("/records/{attendance_id}", response_model=AttendanceRead)
def update_attendance(
    attendance_id: UUID,
    payload: AttendanceUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an attendance record."""
    svc = AttendanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    attendance = svc.update_attendance(organization_id, attendance_id, **update_data)
    db.commit()
    return AttendanceRead.model_validate(attendance)


@router.delete("/records/{attendance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attendance(
    attendance_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete an attendance record."""
    svc = AttendanceService(db)
    svc.delete_attendance(organization_id, attendance_id)
    db.commit()


# =============================================================================
# Check-in / Check-out Actions
# =============================================================================


@router.post("/check-in", response_model=AttendanceRead)
def check_in(
    payload: CheckInRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record employee check-in."""
    svc = AttendanceService(db)
    attendance = svc.check_in(
        org_id=organization_id,
        employee_id=payload.employee_id,
        check_in_time=payload.check_in_time or datetime.now(),
        shift_type_id=payload.shift_type_id,
        notes=payload.notes,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.commit()
    return AttendanceRead.model_validate(attendance)


@router.post("/check-out", response_model=AttendanceRead)
def check_out(
    payload: CheckOutRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record employee check-out."""
    svc = AttendanceService(db)
    attendance = svc.check_out(
        org_id=organization_id,
        employee_id=payload.employee_id,
        check_out_time=payload.check_out_time or datetime.now(),
        notes=payload.notes,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.commit()
    return AttendanceRead.model_validate(attendance)


@router.post("/records/{attendance_id}/check-in", response_model=AttendanceRead)
def check_in_record(
    attendance_id: UUID,
    payload: AttendanceRecordCheckIn,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record check-in for an existing attendance record."""
    svc = AttendanceService(db)
    attendance = svc.check_in_by_attendance_id(
        organization_id,
        attendance_id,
        check_in_time=payload.check_in_time,
        notes=payload.notes,
    )
    db.commit()
    return AttendanceRead.model_validate(attendance)


@router.post("/records/{attendance_id}/check-out", response_model=AttendanceRead)
def check_out_record(
    attendance_id: UUID,
    payload: AttendanceRecordCheckOut,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record check-out for an existing attendance record."""
    svc = AttendanceService(db)
    attendance = svc.check_out_by_attendance_id(
        organization_id,
        attendance_id,
        check_out_time=payload.check_out_time,
        notes=payload.notes,
    )
    db.commit()
    return AttendanceRead.model_validate(attendance)


# =============================================================================
# Bulk Operations
# =============================================================================


@router.post("/bulk-mark")
def bulk_mark_attendance(
    payload: BulkAttendanceCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Bulk mark attendance for multiple employees."""
    svc = AttendanceService(db)
    result = svc.bulk_mark_attendance(
        org_id=organization_id,
        employee_ids=payload.employee_ids,
        attendance_date=payload.attendance_date,
        status=payload.status,
        shift_type_id=payload.shift_type_id,
        remarks=payload.remarks,
    )
    db.commit()
    return {
        "success_count": result["success_count"],
        "failed_count": result["failed_count"],
        "errors": result.get("errors", []),
    }


# =============================================================================
# Attendance Requests
# =============================================================================


@router.get("/requests", response_model=AttendanceRequestListResponse)
def list_attendance_requests(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    status: Optional[AttendanceRequestStatus] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List attendance requests."""
    svc = AttendanceService(db)
    result = svc.list_attendance_requests(
        org_id=organization_id,
        employee_id=employee_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return AttendanceRequestListResponse(
        items=[AttendanceRequestRead.model_validate(r) for r in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/requests", response_model=AttendanceRequestRead, status_code=status.HTTP_201_CREATED)
def create_attendance_request(
    payload: AttendanceRequestCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create an attendance request."""
    svc = AttendanceService(db)
    request = svc.create_attendance_request(
        org_id=organization_id,
        employee_id=payload.employee_id,
        from_date=payload.from_date,
        to_date=payload.to_date,
        half_day=payload.half_day,
        half_day_date=payload.half_day_date,
        reason=payload.reason,
        explanation=payload.explanation,
    )
    db.commit()
    return AttendanceRequestRead.model_validate(request)


@router.get("/requests/{request_id}", response_model=AttendanceRequestRead)
def get_attendance_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get attendance request detail."""
    svc = AttendanceService(db)
    return AttendanceRequestRead.model_validate(
        svc.get_attendance_request(organization_id, request_id)
    )


@router.patch("/requests/{request_id}", response_model=AttendanceRequestRead)
def update_attendance_request(
    request_id: UUID,
    payload: AttendanceRequestUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an attendance request."""
    svc = AttendanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    request = svc.update_attendance_request(organization_id, request_id, **update_data)
    db.commit()
    return AttendanceRequestRead.model_validate(request)


@router.delete("/requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attendance_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete an attendance request."""
    svc = AttendanceService(db)
    svc.delete_attendance_request(organization_id, request_id)
    db.commit()


@router.post("/requests/{request_id}/submit", response_model=AttendanceRequestRead)
def submit_attendance_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit an attendance request for approval."""
    svc = AttendanceService(db)
    request = svc.submit_attendance_request(organization_id, request_id)
    db.commit()
    return AttendanceRequestRead.model_validate(request)


@router.post("/requests/{request_id}/approve", response_model=AttendanceRequestRead)
def approve_attendance_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Approve an attendance request."""
    svc = AttendanceService(db)
    request = svc.approve_attendance_request(organization_id, request_id)
    db.commit()
    return AttendanceRequestRead.model_validate(request)


@router.post("/requests/{request_id}/reject", response_model=AttendanceRequestRead)
def reject_attendance_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Reject an attendance request."""
    svc = AttendanceService(db)
    request = svc.reject_attendance_request(organization_id, request_id)
    db.commit()
    return AttendanceRequestRead.model_validate(request)


@router.post("/requests/bulk/approve")
def bulk_approve_attendance_requests(
    payload: AttendanceRequestBulkAction,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Bulk approve attendance requests."""
    svc = AttendanceService(db)
    result = svc.bulk_approve_attendance_requests(organization_id, payload.request_ids)
    db.commit()
    return result


@router.post("/requests/bulk/reject")
def bulk_reject_attendance_requests(
    payload: AttendanceRequestBulkAction,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Bulk reject attendance requests."""
    svc = AttendanceService(db)
    result = svc.bulk_reject_attendance_requests(organization_id, payload.request_ids)
    db.commit()
    return result


# =============================================================================
# Reporting
# =============================================================================


@router.get("/summary")
def attendance_summary(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Get attendance summary counts."""
    svc = AttendanceService(db)
    return svc.get_attendance_summary(
        org_id=organization_id,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/export")
def export_attendance(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Export attendance records to CSV."""
    svc = AttendanceService(db)
    status_value = None
    if status:
        try:
            status_value = AttendanceStatus(status)
        except ValueError:
            status_value = None
    result = svc.list_attendance(
        org_id=organization_id,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        status=status_value,
        pagination=PaginationParams(offset=0, limit=10000),
    )

    rows = [
        [
            "attendance_id",
            "employee_id",
            "attendance_date",
            "status",
            "check_in",
            "check_out",
            "working_hours",
            "late_entry",
            "early_exit",
        ]
    ]
    for record in result.items:
        rows.append(
            [
                str(record.attendance_id),
                str(record.employee_id),
                record.attendance_date.isoformat(),
                record.status.value if record.status else "",
                record.check_in.isoformat() if record.check_in else "",
                record.check_out.isoformat() if record.check_out else "",
                str(record.working_hours or ""),
                str(bool(record.late_entry)),
                str(bool(record.early_exit)),
            ]
        )

    return csv_response(rows, "attendances.csv")


@router.get("/employees/{employee_id}/summary")
def get_employee_monthly_summary(
    employee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Get monthly attendance summary for an employee."""
    svc = AttendanceService(db)
    summary = svc.get_employee_monthly_summary(
        org_id=organization_id,
        employee_id=employee_id,
        year=year,
        month=month,
    )
    return summary
