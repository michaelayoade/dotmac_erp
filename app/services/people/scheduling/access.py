"""Department-scoped scheduler authorization helpers."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.people.hr.org_resolver import OrgResolver


class SchedulerAccessError(Exception):
    """Raised when a user cannot access a scheduler scope."""


class SchedulerAccessService:
    """Resolve departments and employees a scheduler actor may manage."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def authorized_department_ids(
        self,
        organization_id: UUID,
        *,
        actor_employee_id: UUID | None,
        can_view_all: bool = False,
    ) -> set[UUID]:
        if can_view_all:
            return set(
                self.db.scalars(
                    select(Department.department_id).where(
                        Department.organization_id == organization_id,
                        Department.is_active.is_(True),
                    )
                ).all()
            )
        if actor_employee_id is None:
            return set()

        department_ids: set[UUID] = set(
            self.db.scalars(
                select(Department.department_id).where(
                    Department.organization_id == organization_id,
                    Department.head_id == actor_employee_id,
                    Department.is_active.is_(True),
                )
            ).all()
        )
        own_department_id = self.db.scalar(
            select(Employee.department_id).where(
                Employee.organization_id == organization_id,
                Employee.employee_id == actor_employee_id,
            )
        )
        if own_department_id:
            department_ids.add(own_department_id)

        for report in OrgResolver(self.db).get_direct_reports(
            actor_employee_id,
            organization_id,
        ):
            if report.department_id:
                department_ids.add(report.department_id)
        return department_ids

    def assert_department_access(
        self,
        organization_id: UUID,
        department_id: UUID,
        *,
        actor_employee_id: UUID | None,
        can_view_all: bool = False,
    ) -> None:
        if department_id not in self.authorized_department_ids(
            organization_id,
            actor_employee_id=actor_employee_id,
            can_view_all=can_view_all,
        ):
            raise SchedulerAccessError("You are not authorized for this department")

    def employees_for_department(
        self,
        organization_id: UUID,
        department_id: UUID,
        *,
        actor_employee_id: UUID | None,
        can_view_all: bool = False,
    ) -> list[Employee]:
        self.assert_department_access(
            organization_id,
            department_id,
            actor_employee_id=actor_employee_id,
            can_view_all=can_view_all,
        )
        return list(
            self.db.scalars(
                select(Employee).where(
                    Employee.organization_id == organization_id,
                    Employee.department_id == department_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
            ).all()
        )
