"""
Project API Extensions for PM Module.

Additional endpoints for project management features.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.schemas.pm import (
    GanttChartData,
    ProjectDashboard,
    ProjectTeamResponse,
    ProjectTimeSummary,
)
from app.services.common import NotFoundError
from app.services.pm import (
    DashboardService,
    GanttService,
    ProjectExpenseService,
    ResourceService,
    TimeEntryService,
)

router = APIRouter(prefix="/projects", tags=["pm-projects"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/{project_id}/dashboard", response_model=ProjectDashboard)
def get_project_dashboard(
    project_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get complete dashboard data for a project."""
    svc = DashboardService(db, organization_id)
    try:
        data = svc.get_project_dashboard(project_id)
        return ProjectDashboard(**data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{project_id}/gantt", response_model=GanttChartData)
def get_gantt_data(
    project_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get Gantt chart data for a project."""
    svc = GanttService(db, organization_id)
    try:
        return svc.get_gantt_data(project_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{project_id}/team", response_model=ProjectTeamResponse)
def get_project_team(
    project_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get team members allocated to a project."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select
    from decimal import Decimal

    # Get project
    project = db.scalars(
        select(Project).where(
            Project.project_id == project_id,
            Project.organization_id == organization_id,
        )
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    svc = ResourceService(db, organization_id)
    allocations = svc.get_project_team(project_id)

    team_members = []
    total_allocation = Decimal("0")

    for alloc in allocations:
        emp = alloc.employee
        emp_name = getattr(emp, "full_name", str(alloc.employee_id)[:8]) if emp else str(alloc.employee_id)[:8]

        team_members.append({
            "employee_id": alloc.employee_id,
            "employee_name": emp_name,
            "role_on_project": alloc.role_on_project,
            "allocation_percent": alloc.allocation_percent,
            "start_date": alloc.start_date,
            "end_date": alloc.end_date,
            "is_active": alloc.is_active,
            "total_hours_logged": Decimal("0"),  # Would need to calculate
        })
        total_allocation += alloc.allocation_percent

    return ProjectTeamResponse(
        project_id=project_id,
        project_name=project.project_name,
        team_members=team_members,
        total_allocation_percent=total_allocation,
    )


@router.get("/{project_id}/time-summary", response_model=ProjectTimeSummary)
def get_time_summary(
    project_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get time tracking summary for a project."""
    from app.models.finance.core_org.project import Project
    from sqlalchemy import select

    # Get project
    project = db.scalars(
        select(Project).where(
            Project.project_id == project_id,
            Project.organization_id == organization_id,
        )
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    svc = TimeEntryService(db, organization_id)
    summary = svc.get_project_time_summary(project_id)

    return ProjectTimeSummary(
        project_id=project_id,
        project_name=project.project_name,
        **{k: v for k, v in summary.items() if k != "project_id"},
    )


@router.get("/{project_id}/expenses")
def get_project_expenses(
    project_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get expense claims linked to a project."""
    svc = ProjectExpenseService(db, organization_id)
    return {
        "project_id": project_id,
        "expenses": svc.get_project_expenses(project_id),
        "summary": svc.get_expense_summary(project_id),
    }
