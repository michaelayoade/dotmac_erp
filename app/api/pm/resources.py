"""
Resource Allocation API Endpoints.

REST API for resource allocation and utilization.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.schemas.pm import (
    EndAllocationRequest,
    ResourceAllocationCreate,
    ResourceAllocationListResponse,
    ResourceAllocationRead,
    ResourceAllocationUpdate,
    ResourceAllocationWithDetails,
    UtilizationSummary,
)
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginationParams,
    ValidationError,
)
from app.services.pm import ResourceService

router = APIRouter(prefix="/resources", tags=["pm-resources"])


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
# Resource Allocation CRUD
# =============================================================================


@router.get("", response_model=ResourceAllocationListResponse)
def list_allocations(
    organization_id: UUID = Depends(require_organization_id),
    project_id: UUID | None = None,
    employee_id: UUID | None = None,
    is_active: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List resource allocations with optional filtering."""
    svc = ResourceService(db, organization_id)
    result = svc.list_allocations(
        project_id=project_id,
        employee_id=employee_id,
        is_active=is_active,
        params=PaginationParams(offset=offset, limit=limit),
    )

    items = []
    for alloc in result.items:
        emp = alloc.employee
        proj = alloc.project
        items.append(
            ResourceAllocationWithDetails(
                allocation_id=alloc.allocation_id,
                organization_id=alloc.organization_id,
                project_id=alloc.project_id,
                employee_id=alloc.employee_id,
                role_on_project=alloc.role_on_project,
                allocation_percent=alloc.allocation_percent,
                start_date=alloc.start_date,
                end_date=alloc.end_date,
                is_active=alloc.is_active,
                cost_rate_per_hour=alloc.cost_rate_per_hour,
                billing_rate_per_hour=alloc.billing_rate_per_hour,
                created_at=alloc.created_at,
                updated_at=alloc.updated_at,
                project_name=proj.project_name if proj else None,
                employee_name=getattr(emp, "full_name", str(alloc.employee_id)[:8])
                if emp
                else None,
                is_current=alloc.is_current,
            )
        )

    return ResourceAllocationListResponse(
        items=items,
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.post(
    "", response_model=ResourceAllocationRead, status_code=status.HTTP_201_CREATED
)
def allocate_resource(
    data: ResourceAllocationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Allocate an employee to a project."""
    svc = ResourceService(db, organization_id)
    try:
        allocation = svc.allocate_resource(data.model_dump())
        return ResourceAllocationRead.model_validate(allocation)
    except (ValidationError, ConflictError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/project/{project_id}", response_model=ResourceAllocationListResponse)
def get_project_allocations(
    project_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get all allocations for a project."""
    svc = ResourceService(db, organization_id)
    allocations = svc.get_project_team(project_id)

    items = []
    for alloc in allocations:
        emp = alloc.employee
        proj = alloc.project
        items.append(
            ResourceAllocationWithDetails(
                allocation_id=alloc.allocation_id,
                organization_id=alloc.organization_id,
                project_id=alloc.project_id,
                employee_id=alloc.employee_id,
                role_on_project=alloc.role_on_project,
                allocation_percent=alloc.allocation_percent,
                start_date=alloc.start_date,
                end_date=alloc.end_date,
                is_active=alloc.is_active,
                cost_rate_per_hour=alloc.cost_rate_per_hour,
                billing_rate_per_hour=alloc.billing_rate_per_hour,
                created_at=alloc.created_at,
                updated_at=alloc.updated_at,
                project_name=proj.project_name if proj else None,
                employee_name=getattr(emp, "full_name", str(alloc.employee_id)[:8])
                if emp
                else None,
                is_current=alloc.is_current,
            )
        )

    return ResourceAllocationListResponse(
        items=items,
        total=len(items),
        offset=0,
        limit=len(items),
    )


@router.get("/employee/{employee_id}", response_model=ResourceAllocationListResponse)
def get_employee_allocations(
    employee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    include_past: bool = False,
    db: Session = Depends(get_db),
):
    """Get all allocations for an employee."""
    svc = ResourceService(db, organization_id)
    allocations = svc.get_employee_allocations(employee_id, include_past=include_past)

    items = []
    for alloc in allocations:
        emp = alloc.employee
        proj = alloc.project
        items.append(
            ResourceAllocationWithDetails(
                allocation_id=alloc.allocation_id,
                organization_id=alloc.organization_id,
                project_id=alloc.project_id,
                employee_id=alloc.employee_id,
                role_on_project=alloc.role_on_project,
                allocation_percent=alloc.allocation_percent,
                start_date=alloc.start_date,
                end_date=alloc.end_date,
                is_active=alloc.is_active,
                cost_rate_per_hour=alloc.cost_rate_per_hour,
                billing_rate_per_hour=alloc.billing_rate_per_hour,
                created_at=alloc.created_at,
                updated_at=alloc.updated_at,
                project_name=proj.project_name if proj else None,
                employee_name=getattr(emp, "full_name", str(alloc.employee_id)[:8])
                if emp
                else None,
                is_current=alloc.is_current,
            )
        )

    return ResourceAllocationListResponse(
        items=items,
        total=len(items),
        offset=0,
        limit=len(items),
    )


@router.get("/employee/{employee_id}/utilization", response_model=UtilizationSummary)
def get_employee_utilization(
    employee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """Get utilization summary for an employee."""
    svc = ResourceService(db, organization_id)
    data = svc.get_utilization(employee_id, start_date, end_date)

    return UtilizationSummary(
        employee_id=data["employee_id"],
        employee_name="",  # Would need to fetch employee name
        period_start=data["period_start"],
        period_end=data["period_end"],
        total_allocation_percent=data["total_allocation_percent"],
        available_percent=100 - data["total_allocation_percent"],
        project_allocations=data.get("project_allocations", []),
    )


@router.get("/{allocation_id}", response_model=ResourceAllocationRead)
def get_allocation(
    allocation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a resource allocation by ID."""
    svc = ResourceService(db, organization_id)
    try:
        allocation = svc.get_allocation_or_raise(allocation_id)
        return ResourceAllocationRead.model_validate(allocation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{allocation_id}", response_model=ResourceAllocationRead)
def update_allocation(
    allocation_id: UUID,
    data: ResourceAllocationUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a resource allocation."""
    svc = ResourceService(db, organization_id)
    try:
        allocation = svc.update_allocation(
            allocation_id, data.model_dump(exclude_unset=True)
        )
        return ResourceAllocationRead.model_validate(allocation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{allocation_id}/end", response_model=ResourceAllocationRead)
def end_allocation(
    allocation_id: UUID,
    data: EndAllocationRequest = None,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """End a resource allocation."""
    svc = ResourceService(db, organization_id)
    try:
        end_date_value = data.end_date if data else None
        allocation = svc.end_allocation(allocation_id, end_date_value)
        return ResourceAllocationRead.model_validate(allocation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{allocation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_allocation(
    allocation_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a resource allocation."""
    svc = ResourceService(db, organization_id)
    try:
        svc.delete_allocation(allocation_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
