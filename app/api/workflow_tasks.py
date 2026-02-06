"""
Workflow task API endpoints.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_tenant_auth
from app.db import SessionLocal
from app.models.workflow_task import WorkflowTaskPriority, WorkflowTaskStatus
from app.schemas.workflow_task import (
    WorkflowTaskRead,
    WorkflowTaskStatusUpdate,
    WorkflowTaskSnoozeRequest,
)
from app.services.common import PaginationParams
from app.services.people.hr.employees import EmployeeService
from app.services.workflow_task_service import WorkflowTaskService

router = APIRouter(prefix="/workflow-tasks", tags=["workflow-tasks"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_enum(value: Optional[str], enum_type, field_name: str):
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field_name}: {value}"
        ) from exc


def _get_employee_id(db: Session, organization_id: UUID, person_id: UUID) -> UUID:
    svc = EmployeeService(db, organization_id)
    employee = svc.get_employee_by_person(person_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee record not found")
    return employee.employee_id


@router.get("/my-tasks")
def list_my_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """List workflow tasks assigned to current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    status_enum = parse_enum(status, WorkflowTaskStatus, "status")
    priority_enum = parse_enum(priority, WorkflowTaskPriority, "priority")
    svc = WorkflowTaskService(db)
    result = svc.list_tasks(
        org_id=organization_id,
        assignee_employee_id=employee_id,
        status=status_enum,
        priority=priority_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return {
        "items": [WorkflowTaskRead.model_validate(t) for t in result.items],
        "total": result.total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/my-tasks/summary")
def my_tasks_summary(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Summary of workflow tasks for current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    svc = WorkflowTaskService(db)
    return svc.summary(organization_id, assignee_employee_id=employee_id)


@router.patch("/{task_id}/status")
def update_task_status(
    task_id: UUID,
    payload: WorkflowTaskStatusUpdate,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Update workflow task status."""
    organization_id = UUID(auth["organization_id"])
    svc = WorkflowTaskService(db)
    task = svc.update_status(organization_id, task_id, payload.status)
    db.commit()
    return WorkflowTaskRead.model_validate(task)


@router.post("/{task_id}/complete")
def complete_task(
    task_id: UUID,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Mark workflow task completed."""
    organization_id = UUID(auth["organization_id"])
    svc = WorkflowTaskService(db)
    task = svc.complete_task(organization_id, task_id)
    db.commit()
    return WorkflowTaskRead.model_validate(task)


@router.post("/{task_id}/snooze")
def snooze_task(
    task_id: UUID,
    payload: WorkflowTaskSnoozeRequest,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Snooze a workflow task."""
    organization_id = UUID(auth["organization_id"])
    svc = WorkflowTaskService(db)
    task = svc.snooze_task(organization_id, task_id, days=payload.days)
    db.commit()
    return WorkflowTaskRead.model_validate(task)
