"""
Assignment API Endpoints.

REST API for vehicle assignment management.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.schemas.fleet.assignment import (
    AssignmentBrief,
    AssignmentCreate,
    AssignmentEnd,
    AssignmentListResponse,
    AssignmentRead,
    AssignmentUpdate,
)
from app.services.common import NotFoundError, PaginationParams, ValidationError
from app.services.fleet.assignment_service import AssignmentService

router = APIRouter(prefix="/assignments", tags=["fleet-assignments"])


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


@router.get("", response_model=AssignmentListResponse)
def list_assignments(
    organization_id: UUID = Depends(require_organization_id),
    vehicle_id: UUID | None = None,
    employee_id: UUID | None = None,
    department_id: UUID | None = None,
    active_only: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List assignments with optional filtering."""
    service = AssignmentService(db, organization_id)
    result = service.list_assignments(
        vehicle_id=vehicle_id,
        employee_id=employee_id,
        department_id=department_id,
        active_only=active_only,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return AssignmentListResponse(
        items=[AssignmentBrief.model_validate(a) for a in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/{assignment_id}", response_model=AssignmentRead)
def get_assignment(
    assignment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get assignment details."""
    service = AssignmentService(db, organization_id)
    try:
        assignment = service.get_or_raise(assignment_id)
        return AssignmentRead.model_validate(assignment)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("", response_model=AssignmentRead, status_code=status.HTTP_201_CREATED)
def create_assignment(
    data: AssignmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a new vehicle assignment."""
    service = AssignmentService(db, organization_id)
    try:
        assignment = service.create(data)
        return AssignmentRead.model_validate(assignment)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{assignment_id}", response_model=AssignmentRead)
def update_assignment(
    assignment_id: UUID,
    data: AssignmentUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an assignment."""
    service = AssignmentService(db, organization_id)
    try:
        assignment = service.update(assignment_id, data)
        return AssignmentRead.model_validate(assignment)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{assignment_id}/end", response_model=AssignmentRead)
def end_assignment(
    assignment_id: UUID,
    data: AssignmentEnd,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """End an active assignment."""
    service = AssignmentService(db, organization_id)
    try:
        assignment = service.end_assignment(assignment_id, data)
        return AssignmentRead.model_validate(assignment)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
