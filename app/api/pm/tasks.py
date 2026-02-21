"""
Task API Endpoints.

REST API for task management.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.pm import TaskPriority, TaskStatus
from app.schemas.pm import (
    TaskAssignRequest,
    TaskCompleteResponse,
    TaskCreate,
    TaskDependencyCreate,
    TaskDependencyListResponse,
    TaskDependencyWithDetails,
    TaskListResponse,
    TaskProgressRequest,
    TaskRead,
    TaskStartResponse,
    TaskUpdate,
)
from app.services.common import ConflictError, NotFoundError, ValidationError
from app.services.pm import TaskService

router = APIRouter(prefix="/tasks", tags=["pm-tasks"])


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
# Task CRUD
# =============================================================================


@router.get("", response_model=TaskListResponse)
def list_tasks(
    organization_id: UUID = Depends(require_organization_id),
    project_id: UUID | None = None,
    status: str | None = None,
    priority: str | None = None,
    assigned_to_id: UUID | None = None,
    parent_task_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List tasks with optional filtering."""
    from app.services.common import PaginationParams

    status_enum = None
    if status:
        try:
            status_enum = TaskStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    priority_enum = None
    if priority:
        try:
            priority_enum = TaskPriority(priority)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {priority}")

    svc = TaskService(db, organization_id)
    result = svc.list_tasks(
        project_id=project_id,
        status=status_enum,
        priority=priority_enum,
        assigned_to_id=assigned_to_id,
        parent_task_id=parent_task_id,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return TaskListResponse(
        items=[TaskRead.model_validate(t) for t in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    data: TaskCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a new task."""
    try:
        svc = TaskService(db, organization_id)
        task = svc.create_task(data.model_dump())
        return TaskRead.model_validate(task)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{task_id}", response_model=TaskRead)
def get_task(
    task_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a task by ID."""
    svc = TaskService(db, organization_id)
    try:
        task = svc.get_task_or_raise(task_id)
        return TaskRead.model_validate(task)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{task_id}", response_model=TaskRead)
def update_task(
    task_id: UUID,
    data: TaskUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a task."""
    svc = TaskService(db, organization_id)
    try:
        task = svc.update_task(task_id, data.model_dump(exclude_unset=True))
        return TaskRead.model_validate(task)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a task (soft delete)."""
    svc = TaskService(db, organization_id)
    try:
        svc.delete_task(task_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# Task Status Operations
# =============================================================================


@router.post("/{task_id}/start", response_model=TaskStartResponse)
def start_task(
    task_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Start a task (transition to IN_PROGRESS)."""
    svc = TaskService(db, organization_id)
    try:
        task = svc.start_task(task_id)
        if task.actual_start_date is None:
            raise HTTPException(status_code=500, detail="Task start date was not set")
        return TaskStartResponse(
            task_id=task.task_id,
            status=task.status,
            actual_start_date=task.actual_start_date,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{task_id}/complete", response_model=TaskCompleteResponse)
def complete_task(
    task_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Complete a task."""
    svc = TaskService(db, organization_id)
    try:
        task = svc.complete_task(task_id)
        if task.actual_end_date is None:
            raise HTTPException(status_code=500, detail="Task end date was not set")
        return TaskCompleteResponse(
            task_id=task.task_id,
            status=task.status,
            actual_end_date=task.actual_end_date,
            progress_percent=task.progress_percent,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/{task_id}/progress", response_model=TaskRead)
def update_progress(
    task_id: UUID,
    data: TaskProgressRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update task progress percentage."""
    svc = TaskService(db, organization_id)
    try:
        task = svc.update_progress(task_id, data.progress_percent)
        return TaskRead.model_validate(task)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{task_id}/assign", response_model=TaskRead)
def assign_task(
    task_id: UUID,
    data: TaskAssignRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Assign task to an employee."""
    svc = TaskService(db, organization_id)
    try:
        task = svc.assign_task(task_id, data.assigned_to_id)
        return TaskRead.model_validate(task)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# Task Dependencies
# =============================================================================


@router.get("/{task_id}/dependencies", response_model=TaskDependencyListResponse)
def get_dependencies(
    task_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get all dependencies of a task."""
    svc = TaskService(db, organization_id)
    try:
        svc.get_task_or_raise(task_id)  # Validate task exists
        deps = svc.get_dependencies(task_id)
        return TaskDependencyListResponse(
            items=[
                TaskDependencyWithDetails(
                    dependency_id=d.dependency_id,
                    task_id=d.task_id,
                    depends_on_task_id=d.depends_on_task_id,
                    dependency_type=d.dependency_type,
                    lag_days=d.lag_days,
                    created_at=d.created_at,
                    depends_on_task_code=d.depends_on_task.task_code,
                    depends_on_task_name=d.depends_on_task.task_name,
                )
                for d in deps
            ],
            total=len(deps),
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{task_id}/dependencies", response_model=TaskDependencyWithDetails)
def add_dependency(
    task_id: UUID,
    data: TaskDependencyCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Add a dependency to a task."""
    svc = TaskService(db, organization_id)
    try:
        dep = svc.add_dependency(
            task_id=task_id,
            depends_on_id=data.depends_on_task_id,
            dependency_type=data.dependency_type,
            lag_days=data.lag_days,
        )
        return TaskDependencyWithDetails(
            dependency_id=dep.dependency_id,
            task_id=dep.task_id,
            depends_on_task_id=dep.depends_on_task_id,
            dependency_type=dep.dependency_type,
            lag_days=dep.lag_days,
            created_at=dep.created_at,
            depends_on_task_code=dep.depends_on_task.task_code,
            depends_on_task_name=dep.depends_on_task.task_name,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValidationError, ConflictError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{task_id}/dependencies/{depends_on_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_dependency(
    task_id: UUID,
    depends_on_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Remove a dependency from a task."""
    svc = TaskService(db, organization_id)
    try:
        svc.remove_dependency(task_id, depends_on_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
