"""
Shift Scheduling API Router.

Thin API wrapper for Shift Scheduling endpoints. All business logic is in services.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    require_current_employee_id,
    require_organization_id,
    require_tenant_auth,
)
from app.db import SessionLocal
from app.models.people.scheduling import RotationType, ScheduleStatus, SwapRequestStatus
from app.schemas.people.scheduling import (
    GenerateScheduleResult,
    PatternAssignmentBulkCreate,
    # Assignments
    PatternAssignmentCreate,
    PatternAssignmentListResponse,
    PatternAssignmentRead,
    PatternAssignmentUpdate,
    ScheduleGenerateRequest,
    SchedulePublishRequest,
    # Patterns
    ShiftPatternCreate,
    ShiftPatternListResponse,
    ShiftPatternRead,
    ShiftPatternUpdate,
    ShiftScheduleListResponse,
    # Schedules
    ShiftScheduleRead,
    ShiftScheduleUpdate,
    # Swap Requests
    SwapRequestCreate,
    SwapRequestListResponse,
    SwapRequestRead,
    SwapRequestReview,
)
from app.services.common import PaginationParams
from app.services.people.scheduling import (
    ScheduleGenerator,
    SchedulingService,
    SwapService,
)
from app.services.people.scheduling.schedule_generator import ScheduleGeneratorError
from app.services.people.scheduling.scheduling_service import (
    PatternAssignmentNotFoundError,
    SchedulingServiceError,
    ShiftPatternNotFoundError,
    ShiftScheduleNotFoundError,
)
from app.services.people.scheduling.swap_service import (
    InvalidSwapTransitionError,
    SwapRequestNotFoundError,
    SwapServiceError,
)


def handle_scheduling_error(e: Exception) -> None:
    """Convert scheduling service errors to appropriate HTTP exceptions."""
    if isinstance(
        e,
        (
            ShiftPatternNotFoundError,
            PatternAssignmentNotFoundError,
            ShiftScheduleNotFoundError,
            SwapRequestNotFoundError,
        ),
    ):
        raise HTTPException(status_code=404, detail=str(e))
    elif isinstance(e, InvalidSwapTransitionError):
        raise HTTPException(status_code=409, detail=str(e))
    elif isinstance(
        e, (SchedulingServiceError, ScheduleGeneratorError, SwapServiceError)
    ):
        raise HTTPException(status_code=400, detail=str(e))
    raise


router = APIRouter(
    prefix="/scheduling",
    tags=["scheduling"],
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


# =============================================================================
# Shift Patterns
# =============================================================================


@router.get("/patterns", response_model=ShiftPatternListResponse)
def list_patterns(
    organization_id: UUID = Depends(require_organization_id),
    search: str | None = None,
    is_active: bool | None = None,
    rotation_type: RotationType | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List shift patterns."""
    svc = SchedulingService(db)
    result = svc.list_patterns(
        org_id=organization_id,
        search=search,
        is_active=is_active,
        rotation_type=rotation_type,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ShiftPatternListResponse(
        items=[ShiftPatternRead.model_validate(p) for p in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/patterns", response_model=ShiftPatternRead, status_code=status.HTTP_201_CREATED
)
def create_pattern(
    payload: ShiftPatternCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a shift pattern."""
    try:
        svc = SchedulingService(db)
        pattern = svc.create_pattern(
            org_id=organization_id,
            pattern_code=payload.pattern_code,
            pattern_name=payload.pattern_name,
            description=payload.description,
            rotation_type=payload.rotation_type,
            cycle_weeks=payload.cycle_weeks,
            work_days=payload.work_days,
            day_work_days=payload.day_work_days,
            night_work_days=payload.night_work_days,
            day_shift_type_id=payload.day_shift_type_id,
            night_shift_type_id=payload.night_shift_type_id,
            is_active=payload.is_active,
        )
        return ShiftPatternRead.model_validate(pattern)
    except Exception as e:
        handle_scheduling_error(e)


@router.get("/patterns/{pattern_id}", response_model=ShiftPatternRead)
def get_pattern(
    pattern_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a shift pattern by ID."""
    try:
        svc = SchedulingService(db)
        return ShiftPatternRead.model_validate(
            svc.get_pattern(organization_id, pattern_id)
        )
    except Exception as e:
        handle_scheduling_error(e)


@router.patch("/patterns/{pattern_id}", response_model=ShiftPatternRead)
def update_pattern(
    pattern_id: UUID,
    payload: ShiftPatternUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a shift pattern."""
    svc = SchedulingService(db)
    update_data = payload.model_dump(exclude_unset=True)
    pattern = svc.update_pattern(organization_id, pattern_id, **update_data)
    return ShiftPatternRead.model_validate(pattern)


@router.delete("/patterns/{pattern_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pattern(
    pattern_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Deactivate a shift pattern."""
    svc = SchedulingService(db)
    svc.delete_pattern(organization_id, pattern_id)


# =============================================================================
# Pattern Assignments
# =============================================================================


@router.get("/assignments", response_model=PatternAssignmentListResponse)
def list_assignments(
    organization_id: UUID = Depends(require_organization_id),
    department_id: UUID | None = None,
    employee_id: UUID | None = None,
    shift_pattern_id: UUID | None = None,
    is_active: bool | None = None,
    effective_date: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List pattern assignments."""
    svc = SchedulingService(db)
    result = svc.list_assignments(
        org_id=organization_id,
        department_id=department_id,
        employee_id=employee_id,
        shift_pattern_id=shift_pattern_id,
        is_active=is_active,
        effective_date=effective_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return PatternAssignmentListResponse(
        items=[PatternAssignmentRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/assignments",
    response_model=PatternAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_assignment(
    payload: PatternAssignmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a pattern assignment."""
    svc = SchedulingService(db)
    assignment = svc.create_assignment(
        org_id=organization_id,
        employee_id=payload.employee_id,
        department_id=payload.department_id,
        shift_pattern_id=payload.shift_pattern_id,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        rotation_week_offset=payload.rotation_week_offset,
        is_active=payload.is_active,
    )
    return PatternAssignmentRead.model_validate(assignment)


@router.post("/assignments/bulk")
def bulk_create_assignments(
    payload: PatternAssignmentBulkCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Bulk create pattern assignments for multiple employees."""
    svc = SchedulingService(db)
    result = svc.bulk_create_assignments(
        org_id=organization_id,
        employee_ids=payload.employee_ids,
        department_id=payload.department_id,
        shift_pattern_id=payload.shift_pattern_id,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        rotation_week_offset=payload.rotation_week_offset,
    )
    return result


@router.get("/assignments/{assignment_id}", response_model=PatternAssignmentRead)
def get_assignment(
    assignment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a pattern assignment by ID."""
    svc = SchedulingService(db)
    return PatternAssignmentRead.model_validate(
        svc.get_assignment(organization_id, assignment_id)
    )


@router.patch("/assignments/{assignment_id}", response_model=PatternAssignmentRead)
def update_assignment(
    assignment_id: UUID,
    payload: PatternAssignmentUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a pattern assignment."""
    svc = SchedulingService(db)
    update_data = payload.model_dump(exclude_unset=True)
    assignment = svc.update_assignment(organization_id, assignment_id, **update_data)
    return PatternAssignmentRead.model_validate(assignment)


@router.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """End a pattern assignment."""
    svc = SchedulingService(db)
    svc.delete_assignment(organization_id, assignment_id)


# =============================================================================
# Shift Schedules
# =============================================================================


@router.get("/schedules", response_model=ShiftScheduleListResponse)
def list_schedules(
    organization_id: UUID = Depends(require_organization_id),
    department_id: UUID | None = None,
    employee_id: UUID | None = None,
    schedule_month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    status: ScheduleStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List shift schedules."""
    svc = SchedulingService(db)
    result = svc.list_schedules(
        org_id=organization_id,
        department_id=department_id,
        employee_id=employee_id,
        schedule_month=schedule_month,
        status=status,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ShiftScheduleListResponse(
        items=[ShiftScheduleRead.model_validate(s) for s in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/schedules/generate", response_model=GenerateScheduleResult)
def generate_schedules(
    payload: ScheduleGenerateRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Generate monthly schedules for a department."""
    try:
        generator = ScheduleGenerator(db)
        result = generator.generate_monthly_schedule(
            org_id=organization_id,
            department_id=payload.department_id,
            year_month=payload.year_month,
        )
        return GenerateScheduleResult(**result)
    except Exception as e:
        handle_scheduling_error(e)


@router.post("/schedules/publish")
def publish_schedules(
    payload: SchedulePublishRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Publish draft schedules for a department."""
    generator = ScheduleGenerator(db)
    count = generator.publish_schedule(
        org_id=organization_id,
        department_id=payload.department_id,
        year_month=payload.year_month,
    )
    return {
        "year_month": payload.year_month,
        "department_id": str(payload.department_id),
        "schedules_published": count,
    }


@router.delete("/schedules/month")
def delete_month_schedules(
    department_id: UUID,
    year_month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete all draft schedules for a month (for regeneration)."""
    generator = ScheduleGenerator(db)
    count = generator.delete_month_schedules(
        org_id=organization_id,
        department_id=department_id,
        year_month=year_month,
    )
    return {
        "year_month": year_month,
        "department_id": str(department_id),
        "schedules_deleted": count,
    }


@router.get("/schedules/{schedule_id}", response_model=ShiftScheduleRead)
def get_schedule(
    schedule_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a shift schedule by ID."""
    svc = SchedulingService(db)
    return ShiftScheduleRead.model_validate(
        svc.get_schedule(organization_id, schedule_id)
    )


@router.patch("/schedules/{schedule_id}", response_model=ShiftScheduleRead)
def update_schedule(
    schedule_id: UUID,
    payload: ShiftScheduleUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a shift schedule entry (DRAFT only)."""
    svc = SchedulingService(db)
    update_data = payload.model_dump(exclude_unset=True)
    schedule = svc.update_schedule(organization_id, schedule_id, **update_data)
    return ShiftScheduleRead.model_validate(schedule)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a shift schedule entry (DRAFT only)."""
    svc = SchedulingService(db)
    svc.delete_schedule(organization_id, schedule_id)


# =============================================================================
# Swap Requests
# =============================================================================


@router.get("/swaps", response_model=SwapRequestListResponse)
def list_swap_requests(
    organization_id: UUID = Depends(require_organization_id),
    status: SwapRequestStatus | None = None,
    requester_id: UUID | None = None,
    target_employee_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List swap requests."""
    svc = SwapService(db)
    result = svc.list_swap_requests(
        org_id=organization_id,
        status=status,
        requester_id=requester_id,
        target_employee_id=target_employee_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return SwapRequestListResponse(
        items=[SwapRequestRead.model_validate(r) for r in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/swaps", response_model=SwapRequestRead, status_code=status.HTTP_201_CREATED
)
def create_swap_request(
    payload: SwapRequestCreate,
    organization_id: UUID = Depends(require_organization_id),
    requester_id: UUID = Depends(require_current_employee_id),
    db: Session = Depends(get_db),
):
    """Create a swap request."""
    try:
        svc = SwapService(db)
        request = svc.create_swap_request(
            org_id=organization_id,
            requester_id=requester_id,
            requester_schedule_id=payload.requester_schedule_id,
            target_schedule_id=payload.target_schedule_id,
            reason=payload.reason,
        )
        return SwapRequestRead.model_validate(request)
    except Exception as e:
        handle_scheduling_error(e)


@router.get("/swaps/my-requests", response_model=SwapRequestListResponse)
def get_my_swap_requests(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID = Depends(require_current_employee_id),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Get swap requests created by the current employee."""
    svc = SwapService(db)
    result = svc.get_my_requests(
        org_id=organization_id,
        employee_id=employee_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return SwapRequestListResponse(
        items=[SwapRequestRead.model_validate(r) for r in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.get("/swaps/pending-acceptance", response_model=SwapRequestListResponse)
def get_pending_acceptance(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID = Depends(require_current_employee_id),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Get swap requests waiting for the current employee's acceptance."""
    svc = SwapService(db)
    result = svc.get_pending_acceptance(
        org_id=organization_id,
        employee_id=employee_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return SwapRequestListResponse(
        items=[SwapRequestRead.model_validate(r) for r in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.get("/swaps/{request_id}", response_model=SwapRequestRead)
def get_swap_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a swap request by ID."""
    svc = SwapService(db)
    return SwapRequestRead.model_validate(
        svc.get_swap_request(organization_id, request_id)
    )


@router.post("/swaps/{request_id}/accept", response_model=SwapRequestRead)
def accept_swap_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID = Depends(require_current_employee_id),
    db: Session = Depends(get_db),
):
    """Target employee accepts the swap request."""
    try:
        svc = SwapService(db)
        request = svc.accept_swap_request(
            org_id=organization_id,
            request_id=request_id,
            accepting_employee_id=employee_id,
        )
        return SwapRequestRead.model_validate(request)
    except Exception as e:
        handle_scheduling_error(e)


@router.post("/swaps/{request_id}/approve", response_model=SwapRequestRead)
def approve_swap_request(
    request_id: UUID,
    payload: SwapRequestReview,
    organization_id: UUID = Depends(require_organization_id),
    manager_id: UUID = Depends(require_current_employee_id),
    db: Session = Depends(get_db),
):
    """Manager approves the swap request."""
    try:
        svc = SwapService(db)
        request = svc.approve_swap_request(
            org_id=organization_id,
            request_id=request_id,
            manager_id=manager_id,
            notes=payload.notes,
        )
        return SwapRequestRead.model_validate(request)
    except Exception as e:
        handle_scheduling_error(e)


@router.post("/swaps/{request_id}/reject", response_model=SwapRequestRead)
def reject_swap_request(
    request_id: UUID,
    payload: SwapRequestReview,
    organization_id: UUID = Depends(require_organization_id),
    manager_id: UUID = Depends(require_current_employee_id),
    db: Session = Depends(get_db),
):
    """Manager rejects the swap request."""
    try:
        svc = SwapService(db)
        request = svc.reject_swap_request(
            org_id=organization_id,
            request_id=request_id,
            manager_id=manager_id,
            notes=payload.notes,
        )
        return SwapRequestRead.model_validate(request)
    except Exception as e:
        handle_scheduling_error(e)


@router.post("/swaps/{request_id}/cancel", response_model=SwapRequestRead)
def cancel_swap_request(
    request_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    requester_id: UUID = Depends(require_current_employee_id),
    db: Session = Depends(get_db),
):
    """Requester cancels the swap request."""
    try:
        svc = SwapService(db)
        request = svc.cancel_swap_request(
            org_id=organization_id,
            request_id=request_id,
            requester_id=requester_id,
        )
        return SwapRequestRead.model_validate(request)
    except Exception as e:
        handle_scheduling_error(e)
