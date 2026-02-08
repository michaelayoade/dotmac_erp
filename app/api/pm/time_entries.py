"""
Time Entry API Endpoints.

REST API for time tracking.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.pm import BillingStatus
from app.schemas.pm import (
    TimeEntryCreate,
    TimeEntryListResponse,
    TimeEntryRead,
    TimeEntryUpdate,
    TimeEntryWithDetails,
    TimesheetDay,
    TimesheetWeek,
)
from app.services.common import NotFoundError, PaginationParams, ValidationError
from app.services.pm import TimeEntryService

router = APIRouter(prefix="/time-entries", tags=["pm-time-entries"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Time Entry CRUD
# =============================================================================


@router.get("", response_model=TimeEntryListResponse)
def list_time_entries(
    organization_id: UUID = Depends(require_organization_id),
    project_id: UUID | None = None,
    task_id: UUID | None = None,
    employee_id: UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    is_billable: bool | None = None,
    billing_status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List time entries with optional filtering."""
    billing_status_enum = None
    if billing_status:
        try:
            billing_status_enum = BillingStatus(billing_status)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid billing_status: {billing_status}"
            )

    svc = TimeEntryService(db, organization_id)
    result = svc.list_entries(
        project_id=project_id,
        task_id=task_id,
        employee_id=employee_id,
        start_date=start_date,
        end_date=end_date,
        is_billable=is_billable,
        billing_status=billing_status_enum,
        params=PaginationParams(offset=offset, limit=limit),
    )

    items = []
    for entry in result.items:
        emp = entry.employee
        proj = entry.project
        task = entry.task
        items.append(
            TimeEntryWithDetails(
                entry_id=entry.entry_id,
                organization_id=entry.organization_id,
                project_id=entry.project_id,
                task_id=entry.task_id,
                employee_id=entry.employee_id,
                entry_date=entry.entry_date,
                hours=entry.hours,
                description=entry.description,
                is_billable=entry.is_billable,
                billing_status=entry.billing_status,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
                project_name=proj.project_name if proj else None,
                task_name=task.task_name if task else None,
                employee_name=getattr(emp, "full_name", str(entry.employee_id)[:8])
                if emp
                else None,
            )
        )

    return TimeEntryListResponse(
        items=items,
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.post("", response_model=TimeEntryRead, status_code=status.HTTP_201_CREATED)
def log_time(
    data: TimeEntryCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Log a new time entry."""
    svc = TimeEntryService(db, organization_id)
    try:
        entry = svc.log_time(data.model_dump())
        db.commit()
        db.refresh(entry)
        return TimeEntryRead.model_validate(entry)
    except (ValidationError, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/my-timesheet", response_model=TimesheetWeek)
def get_my_timesheet(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: UUID = Query(...),
    week_start: date = Query(...),
    db: Session = Depends(get_db),
):
    """Get weekly timesheet for an employee."""
    from datetime import timedelta
    from decimal import Decimal

    svc = TimeEntryService(db, organization_id)
    entries = svc.get_employee_timesheet(employee_id, week_start)

    # Group entries by date
    week_end = week_start + timedelta(days=6)
    days_map: dict[date, list[TimeEntryWithDetails]] = {}
    for i in range(7):
        d = week_start + timedelta(days=i)
        days_map[d] = []

    total_hours = Decimal("0")
    billable_hours = Decimal("0")

    for entry in entries:
        if entry.entry_date in days_map:
            days_map[entry.entry_date].append(
                TimeEntryWithDetails(
                    entry_id=entry.entry_id,
                    organization_id=entry.organization_id,
                    project_id=entry.project_id,
                    task_id=entry.task_id,
                    employee_id=entry.employee_id,
                    entry_date=entry.entry_date,
                    hours=entry.hours,
                    description=entry.description,
                    is_billable=entry.is_billable,
                    billing_status=entry.billing_status,
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                    project_name=entry.project.project_name if entry.project else None,
                    task_name=entry.task.task_name if entry.task else None,
                    employee_name=None,
                )
            )
        total_hours += entry.hours
        if entry.is_billable:
            billable_hours += entry.hours

    days: list[TimesheetDay] = []
    for d in sorted(days_map.keys()):
        day_entries = days_map[d]
        day_total = sum((e.hours for e in day_entries), Decimal("0"))
        days.append(
            TimesheetDay(
                date=d,
                entries=day_entries,
                total_hours=day_total,
            )
        )

    return TimesheetWeek(
        employee_id=employee_id,
        employee_name="",  # Would need to fetch
        week_start=week_start,
        week_end=week_end,
        days=days,
        total_hours=total_hours,
        billable_hours=billable_hours,
    )


@router.get("/{entry_id}", response_model=TimeEntryRead)
def get_time_entry(
    entry_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a time entry by ID."""
    svc = TimeEntryService(db, organization_id)
    try:
        entry = svc.get_entry_or_raise(entry_id)
        return TimeEntryRead.model_validate(entry)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{entry_id}", response_model=TimeEntryRead)
def update_time_entry(
    entry_id: UUID,
    data: TimeEntryUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a time entry."""
    svc = TimeEntryService(db, organization_id)
    try:
        entry = svc.update_entry(entry_id, data.model_dump(exclude_unset=True))
        db.commit()
        db.refresh(entry)
        return TimeEntryRead.model_validate(entry)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_time_entry(
    entry_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a time entry."""
    svc = TimeEntryService(db, organization_id)
    try:
        svc.delete_entry(entry_id)
        db.commit()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
