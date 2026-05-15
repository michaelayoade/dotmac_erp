"""
People assets API router.

Provides assignment endpoints for HR asset tracking.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id, require_tenant_auth
from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
from app.schemas.finance.common import ListResponse
from app.models.fixed_assets.maintenance_request import MaintenanceRequestStatus
from app.models.fixed_assets.maintenance_work_order import MaintenanceWorkOrderStatus
from app.models.people.assets.assignment import AssignmentMovementType, AssignmentStatus
from app.models.people.assets.audit import (
    AssetAuditLineStatus,
    AssetAuditPlanStatus,
)
from app.models.people.assets.tracking import AssetTrackingMethod
from app.schemas.people.assets import (
    AssetAuditAdjustmentCreate,
    AssetAuditAdjustmentRead,
    AssetAuditLineCheckRequest,
    AssetAuditLineRead,
    AssetAuditPlanCreate,
    AssetAuditPlanRead,
    AssetAuditDiscrepancyRead,
    AssetLifecycleEventRead,
    AssetAssignmentMovementRead,
    AssetAssignmentReassignRequest,
    AssetDepreciationRunCreate,
    AssetDepreciationRunRead,
    AssetDepreciationScheduleRead,
    AssetAvailableRead,
    AssetAssignmentCreate,
    AssetAssignmentListResponse,
    AssetTrackingEventCreate,
    AssetTrackingEventRead,
    AssetLocationMoveRequest,
    AssetAssignmentRead,
    AssetAssignmentReturnRequest,
    AssetAssignmentTransferRequest,
    MaintenancePartsUseRequest,
    MaintenancePartsUseResult,
    MaintenanceRequestCreate,
    MaintenanceRequestRead,
    MaintenanceWorkOrderCompleteRequest,
    MaintenanceWorkOrderCreate,
    MaintenanceWorkOrderPartRead,
    MaintenanceWorkOrderRead,
)
from app.services.common import PaginationParams
from app.services.people.assets import (
    AssetAssignmentService,
    AssetAuditService,
    AssetMaintenanceService,
    AssetTrackingService,
    PeopleAssetDepreciationService,
)

router = APIRouter(
    prefix="/assets",
    tags=["assets"],
    dependencies=[Depends(require_tenant_auth)],
)


def parse_enum(value: str | None, enum_type, field_name: str):
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field_name}: {value}"
        ) from exc


@router.get("/assignments", response_model=AssetAssignmentListResponse)
def list_assignments(
    organization_id: UUID = Depends(require_organization_id),
    asset_id: UUID | None = None,
    employee_id: UUID | None = None,
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List asset assignments."""
    svc = AssetAssignmentService(db)
    status_enum = parse_enum(status, AssignmentStatus, "status")
    result = svc.list_assignments(
        org_id=organization_id,
        asset_id=asset_id,
        employee_id=employee_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return AssetAssignmentListResponse(
        items=[AssetAssignmentRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.get("/assignments/available", response_model=ListResponse[AssetAvailableRead])
def list_available_assets(
    organization_id: UUID = Depends(require_organization_id),
    location_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List available (unassigned) assets."""
    svc = AssetAssignmentService(db)
    result = svc.list_available_assets(
        org_id=organization_id,
        location_id=location_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[AssetAvailableRead.model_validate(a) for a in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/assignments/movements", response_model=ListResponse[AssetAssignmentMovementRead]
)
def list_assignment_movements(
    organization_id: UUID = Depends(require_organization_id),
    asset_id: UUID | None = None,
    employee_id: UUID | None = None,
    movement_type: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List assignment and location movement history."""
    svc = AssetAssignmentService(db)
    movement_type_enum = parse_enum(
        movement_type, AssignmentMovementType, "movement_type"
    )
    result = svc.list_assignment_movements(
        org_id=organization_id,
        asset_id=asset_id,
        employee_id=employee_id,
        movement_type=movement_type_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[AssetAssignmentMovementRead.model_validate(m) for m in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.get("/tracking/events", response_model=ListResponse[AssetTrackingEventRead])
def list_tracking_events(
    organization_id: UUID = Depends(require_organization_id),
    asset_id: UUID | None = None,
    tracking_method: str | None = None,
    location_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List asset tracking events (QR/Barcode, RFID, GPS)."""
    svc = AssetTrackingService(db)
    method_enum = parse_enum(tracking_method, AssetTrackingMethod, "tracking_method")
    result = svc.list_events(
        org_id=organization_id,
        asset_id=asset_id,
        tracking_method=method_enum,
        location_id=location_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[AssetTrackingEventRead.model_validate(item) for item in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/tracking/events",
    response_model=AssetTrackingEventRead,
    status_code=status.HTTP_201_CREATED,
)
def record_tracking_event(
    payload: AssetTrackingEventCreate,
    organization_id: UUID = Depends(require_organization_id),
    scanned_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Record a new asset tracking event and update location when provided."""
    svc = AssetTrackingService(db)
    event = svc.record_event(
        org_id=organization_id,
        asset_id=payload.asset_id,
        tracking_method=payload.tracking_method,
        tracked_at=payload.tracked_at,
        tracking_reference=payload.tracking_reference,
        location_id=payload.location_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_meters=payload.accuracy_meters,
        notes=payload.notes,
        scanned_by_user_id=scanned_by_user_id,
    )
    return AssetTrackingEventRead.model_validate(event)


@router.get("/audit/plans", response_model=ListResponse[AssetAuditPlanRead])
def list_asset_audit_plans(
    organization_id: UUID = Depends(require_organization_id),
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List asset audit plans."""
    svc = AssetAuditService(db)
    status_enum = parse_enum(status, AssetAuditPlanStatus, "status")
    result = svc.list_plans(
        org_id=organization_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[AssetAuditPlanRead.model_validate(item) for item in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/audit/plans",
    response_model=AssetAuditPlanRead,
    status_code=status.HTTP_201_CREATED,
)
def create_asset_audit_plan(
    payload: AssetAuditPlanCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Create an asset audit plan and snapshot expected asset state."""
    svc = AssetAuditService(db)
    plan = svc.create_plan(
        org_id=organization_id,
        title=payload.title,
        planned_date=payload.planned_date,
        scope_location_id=payload.scope_location_id,
        asset_ids=payload.asset_ids,
        created_by_user_id=created_by_user_id,
    )
    return AssetAuditPlanRead.model_validate(plan)


@router.post("/audit/plans/{audit_plan_id}/start", response_model=AssetAuditPlanRead)
def start_asset_audit_plan(
    audit_plan_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Start an audit plan."""
    svc = AssetAuditService(db)
    plan = svc.start_plan(organization_id, audit_plan_id)
    return AssetAuditPlanRead.model_validate(plan)


@router.get(
    "/audit/plans/{audit_plan_id}/lines",
    response_model=ListResponse[AssetAuditLineRead],
)
def list_asset_audit_lines(
    audit_plan_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db_with_org),
):
    """List audit lines for a plan."""
    svc = AssetAuditService(db)
    status_enum = parse_enum(status, AssetAuditLineStatus, "status")
    result = svc.list_lines(
        organization_id,
        audit_plan_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[AssetAuditLineRead.model_validate(item) for item in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/audit/plans/{audit_plan_id}/discrepancies",
    response_model=ListResponse[AssetAuditDiscrepancyRead],
)
def list_asset_audit_discrepancies(
    audit_plan_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db_with_org),
):
    """List discrepancy records captured for an audit plan."""
    svc = AssetAuditService(db)
    result = svc.list_discrepancies(
        organization_id,
        audit_plan_id,
        status=status,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[AssetAuditDiscrepancyRead.model_validate(item) for item in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.get("/lifecycle-events", response_model=ListResponse[AssetLifecycleEventRead])
def list_asset_lifecycle_events(
    organization_id: UUID = Depends(require_organization_id),
    asset_id: UUID | None = None,
    event_category: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db_with_org),
):
    """List asset state/location/ownership/maintenance lifecycle events."""
    svc = AssetAuditService(db)
    result = svc.list_lifecycle_events(
        organization_id,
        asset_id=asset_id,
        event_category=event_category,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[AssetLifecycleEventRead.model_validate(item) for item in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.post("/audit/lines/{audit_line_id}/check", response_model=AssetAuditLineRead)
def record_asset_audit_check(
    audit_line_id: UUID,
    payload: AssetAuditLineCheckRequest,
    organization_id: UUID = Depends(require_organization_id),
    checked_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Record physical verification of one asset line."""
    svc = AssetAuditService(db)
    line = svc.record_check(
        organization_id,
        audit_line_id,
        is_found=payload.is_found,
        observed_location_id=payload.observed_location_id,
        observed_custodian_employee_id=payload.observed_custodian_employee_id,
        observed_status=payload.observed_status,
        discrepancy_notes=payload.discrepancy_notes,
        checked_by_user_id=checked_by_user_id,
    )
    return AssetAuditLineRead.model_validate(line)


@router.post("/audit/plans/{audit_plan_id}/complete", response_model=AssetAuditPlanRead)
def complete_asset_audit_plan(
    audit_plan_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Complete audit and compute discrepancy metrics."""
    svc = AssetAuditService(db)
    plan = svc.complete_plan(organization_id, audit_plan_id)
    return AssetAuditPlanRead.model_validate(plan)


@router.post(
    "/audit/lines/{audit_line_id}/adjust",
    response_model=AssetAuditAdjustmentRead,
    status_code=status.HTTP_201_CREATED,
)
def apply_asset_audit_adjustment(
    audit_line_id: UUID,
    payload: AssetAuditAdjustmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    applied_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Apply discrepancy adjustment action."""
    svc = AssetAuditService(db)
    adjustment = svc.apply_adjustment(
        organization_id,
        audit_line_id,
        adjustment_type=payload.adjustment_type,
        new_value=payload.new_value,
        notes=payload.notes,
        applied_by_user_id=applied_by_user_id,
    )
    return AssetAuditAdjustmentRead.model_validate(adjustment)


@router.post(
    "/assignments/issue",
    response_model=AssetAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def issue_asset(
    payload: AssetAssignmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    moved_by_user_id: UUID | None = Query(default=None),
    location_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Issue an asset to an employee."""
    svc = AssetAssignmentService(db)
    assignment = svc.issue_asset(
        org_id=organization_id,
        asset_id=payload.asset_id,
        employee_id=payload.employee_id,
        issued_on=payload.issued_on,
        expected_return_date=payload.expected_return_date,
        condition_on_issue=payload.condition_on_issue,
        notes=payload.notes,
        location_id=location_id,
        moved_by_user_id=moved_by_user_id,
    )
    return AssetAssignmentRead.model_validate(assignment)


@router.post("/assignments/{assignment_id}/return", response_model=AssetAssignmentRead)
def return_asset(
    assignment_id: UUID,
    payload: AssetAssignmentReturnRequest,
    organization_id: UUID = Depends(require_organization_id),
    moved_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Return an assigned asset."""
    svc = AssetAssignmentService(db)
    assignment = svc.return_asset(
        org_id=organization_id,
        assignment_id=assignment_id,
        returned_on=payload.returned_on,
        condition_on_return=payload.condition_on_return,
        notes=payload.notes,
        moved_by_user_id=moved_by_user_id,
    )
    return AssetAssignmentRead.model_validate(assignment)


@router.post(
    "/assignments/{assignment_id}/transfer", response_model=AssetAssignmentRead
)
def transfer_asset(
    assignment_id: UUID,
    payload: AssetAssignmentTransferRequest,
    organization_id: UUID = Depends(require_organization_id),
    moved_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Transfer an asset to another employee."""
    svc = AssetAssignmentService(db)
    assignment = svc.transfer_asset(
        org_id=organization_id,
        assignment_id=assignment_id,
        new_employee_id=payload.new_employee_id,
        issued_on=payload.issued_on,
        expected_return_date=payload.expected_return_date,
        condition_on_issue=payload.condition_on_issue,
        notes=payload.notes,
        new_location_id=payload.new_location_id,
        moved_by_user_id=moved_by_user_id,
    )
    return AssetAssignmentRead.model_validate(assignment)


@router.post(
    "/assignments/{assignment_id}/reassign", response_model=AssetAssignmentRead
)
def reassign_asset(
    assignment_id: UUID,
    payload: AssetAssignmentReassignRequest,
    organization_id: UUID = Depends(require_organization_id),
    moved_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Reassign an asset to another employee/location."""
    svc = AssetAssignmentService(db)
    assignment = svc.reassign_asset(
        org_id=organization_id,
        assignment_id=assignment_id,
        new_employee_id=payload.new_employee_id,
        issued_on=payload.issued_on,
        expected_return_date=payload.expected_return_date,
        condition_on_issue=payload.condition_on_issue,
        notes=payload.notes,
        new_location_id=payload.new_location_id,
        moved_by_user_id=moved_by_user_id,
    )
    return AssetAssignmentRead.model_validate(assignment)


@router.post("/{asset_id}/move-location", response_model=AssetAvailableRead)
def move_asset_location(
    asset_id: UUID,
    payload: AssetLocationMoveRequest,
    organization_id: UUID = Depends(require_organization_id),
    moved_by_user_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db_with_org),
):
    """Move an asset between locations and log the movement."""
    svc = AssetAssignmentService(db)
    asset = svc.move_asset_location(
        org_id=organization_id,
        asset_id=asset_id,
        new_location_id=payload.new_location_id,
        moved_on=payload.moved_on,
        notes=payload.notes,
        moved_by_user_id=moved_by_user_id,
    )
    return AssetAvailableRead.model_validate(asset)


@router.post(
    "/depreciation/run",
    response_model=AssetDepreciationRunRead,
    status_code=status.HTTP_201_CREATED,
)
def run_depreciation(
    payload: AssetDepreciationRunCreate,
    run_by_user_id: UUID = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Create and calculate a depreciation run."""
    svc = PeopleAssetDepreciationService(db)
    run = svc.run_depreciation(
        organization_id,
        fiscal_period_id=payload.fiscal_period_id,
        run_by_user_id=run_by_user_id,
        description=payload.description,
    )
    return AssetDepreciationRunRead.model_validate(run)


@router.get("/depreciation/runs", response_model=ListResponse[AssetDepreciationRunRead])
def list_depreciation_runs(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_period_id: UUID | None = None,
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List depreciation runs."""
    svc = PeopleAssetDepreciationService(db)
    status_enum = parse_enum(status, DepreciationRunStatus, "status")
    runs = svc.list_runs(
        organization_id,
        fiscal_period_id=fiscal_period_id,
        status=status_enum,
        offset=offset,
        limit=limit,
    )
    return ListResponse(items=runs, count=len(runs), limit=limit, offset=offset)


@router.post(
    "/depreciation/runs/{run_id}/calculate",
    response_model=AssetDepreciationRunRead,
)
def calculate_depreciation_run(
    run_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Calculate depreciation for an existing run."""
    svc = PeopleAssetDepreciationService(db)
    run = svc.calculate_run(organization_id, run_id)
    return AssetDepreciationRunRead.model_validate(run)


@router.post(
    "/depreciation/runs/{run_id}/post", response_model=AssetDepreciationRunRead
)
def post_depreciation_run(
    run_id: UUID,
    posted_by_user_id: UUID = Query(...),
    posting_date: date | None = Query(default=None),
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """Post depreciation run and update asset balances."""
    svc = PeopleAssetDepreciationService(db)
    run = svc.post_run(
        organization_id,
        run_id,
        posted_by_user_id=posted_by_user_id,
        posting_date=posting_date,
    )
    return AssetDepreciationRunRead.model_validate(run)


@router.get(
    "/depreciation/runs/{run_id}/schedules",
    response_model=ListResponse[AssetDepreciationScheduleRead],
)
def list_depreciation_run_schedules(
    run_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
):
    """List depreciation schedules for a run."""
    svc = PeopleAssetDepreciationService(db)
    schedules = svc.list_run_schedules(organization_id, run_id)
    return ListResponse(
        items=schedules,
        count=len(schedules),
        limit=len(schedules),
        offset=0,
    )


@router.get(
    "/maintenance/requests", response_model=ListResponse[MaintenanceRequestRead]
)
def list_maintenance_requests(
    organization_id: UUID = Depends(require_organization_id),
    asset_id: UUID | None = None,
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List maintenance requests."""
    svc = AssetMaintenanceService(db)
    status_enum = parse_enum(status, MaintenanceRequestStatus, "status")
    result = svc.list_requests(
        org_id=organization_id,
        asset_id=asset_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[MaintenanceRequestRead.model_validate(item) for item in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/maintenance/requests",
    response_model=MaintenanceRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def create_maintenance_request(
    payload: MaintenanceRequestCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db_with_org),
):
    """Create maintenance request."""
    svc = AssetMaintenanceService(db)
    request = svc.create_request(
        org_id=organization_id,
        asset_id=payload.asset_id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        due_date=payload.due_date,
        requested_by_user_id=payload.requested_by_user_id,
        created_by_user_id=created_by_user_id,
    )
    return MaintenanceRequestRead.model_validate(request)


@router.get(
    "/maintenance/work-orders",
    response_model=ListResponse[MaintenanceWorkOrderRead],
)
def list_maintenance_work_orders(
    organization_id: UUID = Depends(require_organization_id),
    maintenance_request_id: UUID | None = None,
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db_with_org),
):
    """List maintenance work orders."""
    svc = AssetMaintenanceService(db)
    status_enum = parse_enum(status, MaintenanceWorkOrderStatus, "status")
    result = svc.list_work_orders(
        org_id=organization_id,
        maintenance_request_id=maintenance_request_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ListResponse(
        items=[MaintenanceWorkOrderRead.model_validate(item) for item in result.items],
        count=result.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/maintenance/work-orders",
    response_model=MaintenanceWorkOrderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_maintenance_work_order(
    payload: MaintenanceWorkOrderCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db_with_org),
):
    """Create maintenance work order from a request."""
    svc = AssetMaintenanceService(db)
    work_order = svc.create_work_order(
        org_id=organization_id,
        maintenance_request_id=payload.maintenance_request_id,
        assigned_to_user_id=payload.assigned_to_user_id,
        planned_start_date=payload.planned_start_date,
        estimated_cost=payload.estimated_cost,
        created_by_user_id=created_by_user_id,
    )
    return MaintenanceWorkOrderRead.model_validate(work_order)


@router.post(
    "/maintenance/work-orders/{work_order_id}/start",
    response_model=MaintenanceWorkOrderRead,
)
def start_maintenance_work_order(
    work_order_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    started_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db_with_org),
):
    """Start maintenance work order."""
    svc = AssetMaintenanceService(db)
    work_order = svc.start_work_order(
        org_id=organization_id,
        work_order_id=work_order_id,
        started_by_user_id=started_by_user_id,
    )
    return MaintenanceWorkOrderRead.model_validate(work_order)


@router.post(
    "/maintenance/work-orders/{work_order_id}/parts/use",
    response_model=MaintenancePartsUseResult,
)
def use_parts_for_maintenance_work_order(
    work_order_id: UUID,
    payload: MaintenancePartsUseRequest,
    organization_id: UUID = Depends(require_organization_id),
    used_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db_with_org),
):
    """Issue available parts and trigger procurement for shortages."""
    svc = AssetMaintenanceService(db)
    result = svc.use_parts(
        org_id=organization_id,
        work_order_id=work_order_id,
        fiscal_period_id=payload.fiscal_period_id,
        warehouse_id=payload.warehouse_id,
        parts=[part.model_dump() for part in payload.parts],
        used_by_user_id=used_by_user_id,
        trigger_procurement=payload.trigger_procurement,
        procurement_requester_id=payload.procurement_requester_id,
        procurement_department_id=payload.procurement_department_id,
    )
    return MaintenancePartsUseResult(
        work_order=MaintenanceWorkOrderRead.model_validate(result["work_order"]),
        request=MaintenanceRequestRead.model_validate(result["request"]),
        used_parts=[
            MaintenanceWorkOrderPartRead.model_validate(item)
            for item in result["used_parts"]
        ],
        pending_parts=[
            MaintenanceWorkOrderPartRead.model_validate(item)
            for item in result["pending_parts"]
        ],
        procurement_requisition_id=result["procurement_requisition_id"],
    )


@router.post(
    "/maintenance/work-orders/{work_order_id}/complete",
    response_model=MaintenanceWorkOrderRead,
)
def complete_maintenance_work_order(
    work_order_id: UUID,
    payload: MaintenanceWorkOrderCompleteRequest,
    organization_id: UUID = Depends(require_organization_id),
    completed_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db_with_org),
):
    """Complete maintenance work order."""
    svc = AssetMaintenanceService(db)
    work_order = svc.complete_work_order(
        org_id=organization_id,
        work_order_id=work_order_id,
        completed_by_user_id=completed_by_user_id,
        completion_notes=payload.completion_notes,
        labor_hours=payload.labor_hours,
        additional_cost=payload.additional_cost,
    )
    return MaintenanceWorkOrderRead.model_validate(work_order)
