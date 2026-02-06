"""
Milestone API Endpoints.

REST API for milestone management.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.pm import MilestoneStatus
from app.schemas.pm import (
    MilestoneAchieveRequest,
    MilestoneAchieveResponse,
    MilestoneCreate,
    MilestoneListResponse,
    MilestoneRead,
    MilestoneUpdate,
    MilestoneWithDetails,
)
from app.services.common import ConflictError, NotFoundError, PaginationParams
from app.services.pm import MilestoneService

router = APIRouter(prefix="/milestones", tags=["pm-milestones"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Milestone CRUD
# =============================================================================


@router.get("", response_model=MilestoneListResponse)
def list_milestones(
    organization_id: UUID = Depends(require_organization_id),
    project_id: Optional[UUID] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List milestones with optional filtering."""
    status_enum = None
    if status:
        try:
            status_enum = MilestoneStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    svc = MilestoneService(db, organization_id)
    result = svc.list_milestones(
        project_id=project_id,
        status=status_enum,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return MilestoneListResponse(
        items=[MilestoneRead.model_validate(m) for m in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.post("", response_model=MilestoneRead, status_code=status.HTTP_201_CREATED)
def create_milestone(
    data: MilestoneCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a new milestone."""
    svc = MilestoneService(db, organization_id)
    milestone = svc.create_milestone(data.model_dump())
    db.commit()
    db.refresh(milestone)
    return MilestoneRead.model_validate(milestone)


@router.get("/upcoming", response_model=List[MilestoneWithDetails])
def get_upcoming_milestones(
    organization_id: UUID = Depends(require_organization_id),
    project_id: Optional[UUID] = None,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Get milestones due within the specified days."""
    from datetime import date

    svc = MilestoneService(db, organization_id)
    milestones = svc.get_upcoming_milestones(days=days, project_id=project_id)

    today = date.today()
    return [
        MilestoneWithDetails(
            milestone_id=m.milestone_id,
            organization_id=m.organization_id,
            project_id=m.project_id,
            milestone_code=m.milestone_code,
            milestone_name=m.milestone_name,
            description=m.description,
            target_date=m.target_date,
            actual_date=m.actual_date,
            status=m.status,
            linked_task_id=m.linked_task_id,
            created_at=m.created_at,
            updated_at=m.updated_at,
            project_name=m.project.project_name if m.project else None,
            linked_task_name=m.linked_task.task_name if m.linked_task else None,
            is_overdue=m.is_overdue,
            days_until_target=(m.target_date - today).days,
        )
        for m in milestones
    ]


@router.get("/overdue", response_model=List[MilestoneWithDetails])
def get_overdue_milestones(
    organization_id: UUID = Depends(require_organization_id),
    project_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Get overdue milestones."""
    from datetime import date

    svc = MilestoneService(db, organization_id)
    milestones = svc.get_overdue_milestones(project_id=project_id)

    today = date.today()
    return [
        MilestoneWithDetails(
            milestone_id=m.milestone_id,
            organization_id=m.organization_id,
            project_id=m.project_id,
            milestone_code=m.milestone_code,
            milestone_name=m.milestone_name,
            description=m.description,
            target_date=m.target_date,
            actual_date=m.actual_date,
            status=m.status,
            linked_task_id=m.linked_task_id,
            created_at=m.created_at,
            updated_at=m.updated_at,
            project_name=m.project.project_name if m.project else None,
            linked_task_name=m.linked_task.task_name if m.linked_task else None,
            is_overdue=True,
            days_until_target=(m.target_date - today).days,
        )
        for m in milestones
    ]


@router.get("/{milestone_id}", response_model=MilestoneRead)
def get_milestone(
    milestone_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a milestone by ID."""
    svc = MilestoneService(db, organization_id)
    try:
        milestone = svc.get_milestone_or_raise(milestone_id)
        return MilestoneRead.model_validate(milestone)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{milestone_id}", response_model=MilestoneRead)
def update_milestone(
    milestone_id: UUID,
    data: MilestoneUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a milestone."""
    svc = MilestoneService(db, organization_id)
    try:
        milestone = svc.update_milestone(
            milestone_id, data.model_dump(exclude_unset=True)
        )
        db.commit()
        db.refresh(milestone)
        return MilestoneRead.model_validate(milestone)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_milestone(
    milestone_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a milestone."""
    svc = MilestoneService(db, organization_id)
    try:
        svc.delete_milestone(milestone_id)
        db.commit()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# Milestone Status Operations
# =============================================================================


@router.post("/{milestone_id}/achieve", response_model=MilestoneAchieveResponse)
def achieve_milestone(
    milestone_id: UUID,
    data: MilestoneAchieveRequest = None,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Mark a milestone as achieved."""
    svc = MilestoneService(db, organization_id)
    try:
        actual_date = data.actual_date if data else None
        milestone = svc.achieve_milestone(milestone_id, actual_date)
        db.commit()
        if milestone.actual_date is None:
            raise HTTPException(
                status_code=500, detail="Milestone actual date was not set"
            )
        return MilestoneAchieveResponse(
            milestone_id=milestone.milestone_id,
            status=milestone.status,
            actual_date=milestone.actual_date,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
