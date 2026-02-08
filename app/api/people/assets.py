"""
People assets API router.

Provides assignment endpoints for HR asset tracking.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.assets.assignment import AssignmentStatus
from app.schemas.people.assets import (
    AssetAssignmentCreate,
    AssetAssignmentListResponse,
    AssetAssignmentRead,
    AssetAssignmentReturnRequest,
    AssetAssignmentTransferRequest,
)
from app.services.common import PaginationParams
from app.services.people.assets import AssetAssignmentService

router = APIRouter(
    prefix="/assets",
    tags=["assets"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    db: Session = Depends(get_db),
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


@router.post(
    "/assignments/issue",
    response_model=AssetAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def issue_asset(
    payload: AssetAssignmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
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
    )
    db.commit()
    return AssetAssignmentRead.model_validate(assignment)


@router.post("/assignments/{assignment_id}/return", response_model=AssetAssignmentRead)
def return_asset(
    assignment_id: UUID,
    payload: AssetAssignmentReturnRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Return an assigned asset."""
    svc = AssetAssignmentService(db)
    assignment = svc.return_asset(
        org_id=organization_id,
        assignment_id=assignment_id,
        returned_on=payload.returned_on,
        condition_on_return=payload.condition_on_return,
        notes=payload.notes,
    )
    db.commit()
    return AssetAssignmentRead.model_validate(assignment)


@router.post(
    "/assignments/{assignment_id}/transfer", response_model=AssetAssignmentRead
)
def transfer_asset(
    assignment_id: UUID,
    payload: AssetAssignmentTransferRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
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
    )
    db.commit()
    return AssetAssignmentRead.model_validate(assignment)
