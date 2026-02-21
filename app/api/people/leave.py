"""
Leave Management API Router.

Thin API wrapper for Leave Management endpoints. All business logic is in services.
"""

import csv
import io
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.leave import LeaveApplicationStatus
from app.schemas.people.leave import (
    BulkLeaveAllocationCreate,
    BulkLeaveAllocationResult,
    HolidayCreate,
    # Holiday List
    HolidayListCreate,
    HolidayListRead,
    HolidayListUpdate,
    HolidayRead,
    # Leave Allocation
    LeaveAllocationCreate,
    LeaveAllocationListResponse,
    LeaveAllocationRead,
    LeaveAllocationUpdate,
    LeaveApplicationBulkAction,
    # Leave Application
    LeaveApplicationCreate,
    LeaveApplicationListResponse,
    LeaveApplicationRead,
    LeaveApplicationUpdate,
    # Leave Type
    LeaveTypeCreate,
    LeaveTypeListResponse,
    LeaveTypeRead,
    LeaveTypeUpdate,
)
from app.services.common import PaginationParams
from app.services.people.leave import LeaveService

router = APIRouter(
    prefix="/leave",
    tags=["leave"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
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
# Leave Types
# =============================================================================


@router.get("/types", response_model=LeaveTypeListResponse)
def list_leave_types(
    organization_id: UUID = Depends(require_organization_id),
    search: str | None = None,
    is_active: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List leave types."""
    svc = LeaveService(db)
    result = svc.list_leave_types(
        org_id=organization_id,
        search=search,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return LeaveTypeListResponse(
        items=[LeaveTypeRead.model_validate(lt) for lt in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/types", response_model=LeaveTypeRead, status_code=status.HTTP_201_CREATED
)
def create_leave_type(
    payload: LeaveTypeCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a leave type."""
    svc = LeaveService(db)
    leave_type = svc.create_leave_type(
        org_id=organization_id,
        leave_type_code=payload.leave_type_code,
        leave_type_name=payload.leave_type_name,
        description=payload.description,
        allocation_policy=payload.allocation_policy,
        max_days_per_year=payload.max_days_per_year,
        max_continuous_days=payload.max_continuous_days,
        allow_carry_forward=payload.allow_carry_forward,
        max_carry_forward_days=payload.max_carry_forward_days,
        carry_forward_expiry_months=payload.carry_forward_expiry_months,
        allow_encashment=payload.allow_encashment,
        encashment_threshold_days=payload.encashment_threshold_days,
        is_lwp=payload.is_lwp,
        is_compensatory=payload.is_compensatory,
        include_holidays=payload.include_holidays,
        applicable_after_days=payload.applicable_after_days,
        is_optional=payload.is_optional,
        max_optional_leaves=payload.max_optional_leaves,
        is_active=payload.is_active,
    )
    return LeaveTypeRead.model_validate(leave_type)


@router.get("/types/{leave_type_id}", response_model=LeaveTypeRead)
def get_leave_type(
    leave_type_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a leave type by ID."""
    svc = LeaveService(db)
    return LeaveTypeRead.model_validate(
        svc.get_leave_type(organization_id, leave_type_id)
    )


@router.patch("/types/{leave_type_id}", response_model=LeaveTypeRead)
def update_leave_type(
    leave_type_id: UUID,
    payload: LeaveTypeUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a leave type."""
    svc = LeaveService(db)
    # Build update kwargs from payload
    update_data = payload.model_dump(exclude_unset=True)
    leave_type = svc.update_leave_type(organization_id, leave_type_id, **update_data)
    return LeaveTypeRead.model_validate(leave_type)


@router.delete("/types/{leave_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_leave_type(
    leave_type_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a leave type."""
    svc = LeaveService(db)
    svc.delete_leave_type(organization_id, leave_type_id)


# =============================================================================
# Holiday Lists
# =============================================================================


@router.get("/holiday-lists")
def list_holiday_lists(
    organization_id: UUID = Depends(require_organization_id),
    year: int | None = None,
    is_active: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List holiday lists."""
    svc = LeaveService(db)
    result = svc.list_holiday_lists(
        org_id=organization_id,
        year=year,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return {
        "items": [HolidayListRead.model_validate(hl) for hl in result.items],
        "total": result.total,
        "offset": offset,
        "limit": limit,
    }


@router.post(
    "/holiday-lists",
    response_model=HolidayListRead,
    status_code=status.HTTP_201_CREATED,
)
def create_holiday_list(
    payload: HolidayListCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a holiday list."""
    svc = LeaveService(db)
    holiday_list = svc.create_holiday_list(
        org_id=organization_id,
        list_code=payload.list_code,
        list_name=payload.list_name,
        description=payload.description,
        year=payload.year,
        from_date=payload.from_date,
        to_date=payload.to_date,
        weekly_off=payload.weekly_off,
        is_default=payload.is_default,
        is_active=payload.is_active,
    )
    # Add holidays if provided
    for h in payload.holidays:
        svc.add_holiday(
            org_id=organization_id,
            holiday_list_id=holiday_list.holiday_list_id,
            holiday_date=h.holiday_date,
            holiday_name=h.holiday_name,
            description=h.description,
            is_optional=h.is_optional,
        )
    return HolidayListRead.model_validate(holiday_list)


@router.get("/holiday-lists/{holiday_list_id}", response_model=HolidayListRead)
def get_holiday_list(
    holiday_list_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a holiday list by ID."""
    svc = LeaveService(db)
    return HolidayListRead.model_validate(
        svc.get_holiday_list(organization_id, holiday_list_id)
    )


@router.patch("/holiday-lists/{holiday_list_id}", response_model=HolidayListRead)
def update_holiday_list(
    holiday_list_id: UUID,
    payload: HolidayListUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a holiday list."""
    svc = LeaveService(db)
    update_data = payload.model_dump(exclude_unset=True)
    holiday_list = svc.update_holiday_list(
        organization_id, holiday_list_id, **update_data
    )
    return HolidayListRead.model_validate(holiday_list)


@router.delete(
    "/holiday-lists/{holiday_list_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_holiday_list(
    holiday_list_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a holiday list."""
    svc = LeaveService(db)
    svc.delete_holiday_list(organization_id, holiday_list_id)


# Holiday sub-endpoints
@router.post(
    "/holiday-lists/{holiday_list_id}/holidays",
    response_model=HolidayRead,
    status_code=status.HTTP_201_CREATED,
)
def add_holiday(
    holiday_list_id: UUID,
    payload: HolidayCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Add a holiday to a holiday list."""
    svc = LeaveService(db)
    holiday = svc.add_holiday(
        org_id=organization_id,
        holiday_list_id=holiday_list_id,
        holiday_date=payload.holiday_date,
        holiday_name=payload.holiday_name,
        description=payload.description,
        is_optional=payload.is_optional,
    )
    return HolidayRead.model_validate(holiday)


@router.delete(
    "/holiday-lists/{holiday_list_id}/holidays/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_holiday(
    holiday_list_id: UUID,
    holiday_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Remove a holiday from a holiday list."""
    svc = LeaveService(db)
    svc.remove_holiday(organization_id, holiday_list_id, holiday_id)


# =============================================================================
# Leave Allocations
# =============================================================================


@router.get("/allocations", response_model=LeaveAllocationListResponse)
def list_allocations(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID | None = None,
    leave_type_id: UUID | None = None,
    year: int | None = None,
    is_active: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List leave allocations."""
    svc = LeaveService(db)
    result = svc.list_allocations(
        org_id=organization_id,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        year=year,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return LeaveAllocationListResponse(
        items=[LeaveAllocationRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.get("/allocations/export")
def export_allocations(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID | None = None,
    leave_type_id: UUID | None = None,
    year: int | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    """Export leave allocations to CSV."""
    svc = LeaveService(db)
    result = svc.list_allocations(
        org_id=organization_id,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        year=year,
        is_active=is_active,
        pagination=PaginationParams(offset=0, limit=10000),
    )

    rows = [
        [
            "allocation_id",
            "employee_id",
            "leave_type_id",
            "from_date",
            "to_date",
            "new_leaves_allocated",
            "carry_forward_leaves",
            "total_leaves_allocated",
            "leaves_used",
            "leaves_encashed",
            "leaves_expired",
            "is_active",
        ]
    ]
    for allocation in result.items:
        rows.append(
            [
                str(allocation.allocation_id),
                str(allocation.employee_id),
                str(allocation.leave_type_id),
                allocation.from_date.isoformat(),
                allocation.to_date.isoformat(),
                str(allocation.new_leaves_allocated),
                str(allocation.carry_forward_leaves),
                str(allocation.total_leaves_allocated),
                str(allocation.leaves_used),
                str(allocation.leaves_encashed),
                str(allocation.leaves_expired),
                str(bool(allocation.is_active)),
            ]
        )

    return csv_response(rows, "leave_allocations.csv")


@router.post(
    "/allocations",
    response_model=LeaveAllocationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_allocation(
    payload: LeaveAllocationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a leave allocation."""
    svc = LeaveService(db)
    allocation = svc.create_allocation(
        org_id=organization_id,
        employee_id=payload.employee_id,
        leave_type_id=payload.leave_type_id,
        from_date=payload.from_date,
        to_date=payload.to_date,
        new_leaves_allocated=payload.new_leaves_allocated,
        carry_forward_leaves=payload.carry_forward_leaves,
        notes=payload.notes,
    )
    return LeaveAllocationRead.model_validate(allocation)


@router.post("/allocations/bulk", response_model=BulkLeaveAllocationResult)
def bulk_create_allocations(
    payload: BulkLeaveAllocationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Bulk create leave allocations for employees."""
    svc = LeaveService(db)
    result = svc.bulk_create_allocations(
        org_id=organization_id,
        employee_ids=payload.employee_ids,
        leave_type_id=payload.leave_type_id,
        from_date=payload.from_date,
        to_date=payload.to_date,
        new_leaves_allocated=payload.new_leaves_allocated,
        carry_forward_leaves=payload.carry_forward_leaves,
        notes=payload.notes,
    )
    return BulkLeaveAllocationResult(**result)


@router.get("/allocations/{allocation_id}", response_model=LeaveAllocationRead)
def get_allocation(
    allocation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a leave allocation by ID."""
    svc = LeaveService(db)
    return LeaveAllocationRead.model_validate(
        svc.get_allocation(organization_id, allocation_id)
    )


@router.patch("/allocations/{allocation_id}", response_model=LeaveAllocationRead)
def update_allocation(
    allocation_id: UUID,
    payload: LeaveAllocationUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a leave allocation."""
    svc = LeaveService(db)
    update_data = payload.model_dump(exclude_unset=True)
    allocation = svc.update_allocation(organization_id, allocation_id, **update_data)
    return LeaveAllocationRead.model_validate(allocation)


@router.delete("/allocations/{allocation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_allocation(
    allocation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a leave allocation."""
    svc = LeaveService(db)
    svc.delete_allocation(organization_id, allocation_id)


# Employee balance endpoint
@router.get("/employees/{employee_id}/balance")
def get_employee_leave_balance(
    employee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date | None = None,
    db: Session = Depends(get_db),
):
    """Get leave balance for an employee."""
    svc = LeaveService(db)
    balances = svc.get_employee_balances(
        org_id=organization_id,
        employee_id=employee_id,
        as_of_date=as_of_date or date.today(),
    )
    return {"employee_id": employee_id, "balances": balances}


@router.get("/balance")
def get_leave_balance(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID = Query(...),
    as_of_date: date | None = None,
    db: Session = Depends(get_db),
):
    """Get leave balances for an employee."""
    svc = LeaveService(db)
    balances = svc.get_employee_balances(
        org_id=organization_id,
        employee_id=employee_id,
        as_of_date=as_of_date or date.today(),
    )
    return {"employee_id": employee_id, "balances": balances}


# =============================================================================
# Leave Applications
# =============================================================================


@router.get("/applications", response_model=LeaveApplicationListResponse)
def list_applications(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID | None = None,
    leave_type_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List leave applications."""
    svc = LeaveService(db)
    status_value = None
    if status:
        try:
            status_value = LeaveApplicationStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc
    result = svc.list_applications(
        org_id=organization_id,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return LeaveApplicationListResponse(
        items=[LeaveApplicationRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/applications",
    response_model=LeaveApplicationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_application(
    payload: LeaveApplicationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a leave application."""
    svc = LeaveService(db)
    application = svc.create_application(
        org_id=organization_id,
        employee_id=payload.employee_id,
        leave_type_id=payload.leave_type_id,
        from_date=payload.from_date,
        to_date=payload.to_date,
        half_day=payload.half_day,
        half_day_date=payload.half_day_date,
        reason=payload.reason,
    )
    return LeaveApplicationRead.model_validate(application)


@router.get("/applications/export")
def export_applications(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID | None = None,
    leave_type_id: UUID | None = None,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
):
    """Export leave applications to CSV."""
    svc = LeaveService(db)
    status_value = None
    if status:
        try:
            status_value = LeaveApplicationStatus(status)
        except ValueError:
            status_value = None
    result = svc.list_applications(
        org_id=organization_id,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=0, limit=10000),
    )
    rows = [
        [
            "application_id",
            "employee_id",
            "leave_type_id",
            "from_date",
            "to_date",
            "total_leave_days",
            "status",
        ]
    ]
    for app in result.items:
        rows.append(
            [
                str(app.application_id),
                str(app.employee_id),
                str(app.leave_type_id),
                app.from_date.isoformat(),
                app.to_date.isoformat(),
                str(app.total_leave_days),
                app.status.value if app.status else "",
            ]
        )
    return csv_response(rows, "leave_applications.csv")


@router.get("/applications/{application_id}", response_model=LeaveApplicationRead)
def get_application(
    application_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a leave application by ID."""
    svc = LeaveService(db)
    return LeaveApplicationRead.model_validate(
        svc.get_application(organization_id, application_id)
    )


@router.patch("/applications/{application_id}", response_model=LeaveApplicationRead)
def update_application(
    application_id: UUID,
    payload: LeaveApplicationUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a leave application (only in draft/submitted status)."""
    svc = LeaveService(db)
    update_data = payload.model_dump(exclude_unset=True)
    application = svc.update_application(organization_id, application_id, **update_data)
    return LeaveApplicationRead.model_validate(application)


@router.delete("/applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    application_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a leave application (only in draft status)."""
    svc = LeaveService(db)
    svc.delete_application(organization_id, application_id)


# =============================================================================
# Leave Application Workflow Actions
# =============================================================================


@router.post(
    "/applications/{application_id}/approve", response_model=LeaveApplicationRead
)
def approve_application(
    application_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approver_id: UUID | None = None,
    notes: str | None = None,
    db: Session = Depends(get_db),
):
    """Approve a leave application."""
    svc = LeaveService(db)
    application = svc.approve_application(
        org_id=organization_id,
        application_id=application_id,
        approver_id=approver_id,
        notes=notes,
    )
    return LeaveApplicationRead.model_validate(application)


@router.post(
    "/applications/{application_id}/reject", response_model=LeaveApplicationRead
)
def reject_application(
    application_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approver_id: UUID | None = None,
    reason: str = Query(...),
    db: Session = Depends(get_db),
):
    """Reject a leave application."""
    svc = LeaveService(db)
    application = svc.reject_application(
        org_id=organization_id,
        application_id=application_id,
        approver_id=approver_id,
        reason=reason,
    )
    return LeaveApplicationRead.model_validate(application)


@router.post(
    "/applications/{application_id}/cancel", response_model=LeaveApplicationRead
)
def cancel_application(
    application_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    reason: str | None = None,
    db: Session = Depends(get_db),
):
    """Cancel a leave application."""
    svc = LeaveService(db)
    application = svc.cancel_application(
        org_id=organization_id,
        application_id=application_id,
        reason=reason,
    )
    return LeaveApplicationRead.model_validate(application)


@router.post("/applications/bulk/approve")
def bulk_approve_applications(
    payload: LeaveApplicationBulkAction,
    organization_id: UUID = Depends(require_organization_id),
    approver_id: UUID | None = None,
    notes: str | None = None,
    db: Session = Depends(get_db),
):
    """Bulk approve leave applications."""
    svc = LeaveService(db)
    result = svc.bulk_approve_applications(
        org_id=organization_id,
        application_ids=payload.application_ids,
        approver_id=approver_id,
        notes=notes,
    )
    return result


@router.post("/applications/bulk/reject")
def bulk_reject_applications(
    payload: LeaveApplicationBulkAction,
    organization_id: UUID = Depends(require_organization_id),
    approver_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    """Bulk reject leave applications."""
    svc = LeaveService(db)
    result = svc.bulk_reject_applications(
        org_id=organization_id,
        application_ids=payload.application_ids,
        approver_id=approver_id,
        reason=payload.reason,
    )
    return result
