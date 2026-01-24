"""
HR Export Services - Push DotMac HR data to ERPNext.

During transition, changes to employees/departments in DotMac
need to sync back to ERPNext to maintain consistency.
"""
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus, Gender
from app.models.sync import SyncEntity, SyncStatus
from app.services.erpnext.client import ERPNextClient

from .base import BaseExportService


# Status mapping: DotMac → ERPNext
EMPLOYEE_STATUS_EXPORT_MAP = {
    EmployeeStatus.DRAFT: "Left",  # ERPNext doesn't have draft - treat as inactive
    EmployeeStatus.ACTIVE: "Active",
    EmployeeStatus.ON_LEAVE: "Active",  # ERPNext doesn't have on_leave
    EmployeeStatus.SUSPENDED: "Left",  # Suspended → Left for ERPNext
    EmployeeStatus.RESIGNED: "Left",
    EmployeeStatus.TERMINATED: "Left",
    EmployeeStatus.RETIRED: "Left",
}

GENDER_EXPORT_MAP = {
    Gender.MALE: "Male",
    Gender.FEMALE: "Female",
    Gender.OTHER: "Other",
    Gender.PREFER_NOT_TO_SAY: "Other",  # ERPNext doesn't have this option
}


class DepartmentExportService(BaseExportService[Department]):
    """Export Departments to ERPNext."""

    target_doctype = "Department"
    source_table = "hr.department"

    def __init__(
        self,
        db: Session,
        client: ERPNextClient,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        company: str,
    ):
        super().__init__(db, client, organization_id, user_id)
        self.company = company  # ERPNext requires company context

    def get_pending_exports(self) -> list[Department]:
        """Get departments that need to be exported."""
        # Find departments that:
        # 1. Don't have an erpnext_id (newly created)
        # 2. Have been modified since last sync
        stmt = select(Department).where(
            Department.organization_id == self.organization_id,
            Department.is_active == True,
            Department.deleted_at.is_(None),
        )
        return list(self.db.execute(stmt).scalars().all())

    def transform_for_export(self, entity: Department) -> dict[str, Any]:
        """Transform Department to ERPNext format."""
        data: dict[str, Any] = {
            "department_name": entity.department_name,
            "company": self.company,
            "is_group": 0,  # Default to not a group
            "disabled": 0 if entity.is_active else 1,
        }

        # Parent department
        if entity.parent_department and entity.parent_department.erpnext_id:
            # ERPNext uses "department_name - company" as parent format
            data["parent_department"] = entity.parent_department.erpnext_id

        return data

    def get_entity_id(self, entity: Department) -> uuid.UUID:
        return entity.department_id

    def get_erpnext_id(self, entity: Department) -> Optional[str]:
        return entity.erpnext_id

    def set_erpnext_id(self, entity: Department, erpnext_id: str) -> None:
        entity.erpnext_id = erpnext_id
        entity.last_synced_at = datetime.utcnow()


class EmployeeExportService(BaseExportService[Employee]):
    """Export Employees to ERPNext."""

    target_doctype = "Employee"
    source_table = "hr.employee"

    def __init__(
        self,
        db: Session,
        client: ERPNextClient,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        company: str,
    ):
        super().__init__(db, client, organization_id, user_id)
        self.company = company  # ERPNext requires company context

    def get_pending_exports(self) -> list[Employee]:
        """Get employees that need to be exported."""
        # Find employees that:
        # 1. Don't have an erpnext_id (newly created)
        # 2. Are ACTIVE status (don't export drafts/terminated)
        stmt = select(Employee).where(
            Employee.organization_id == self.organization_id,
            Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            Employee.deleted_at.is_(None),
        )
        return list(self.db.execute(stmt).scalars().all())

    def transform_for_export(self, entity: Employee) -> dict[str, Any]:
        """Transform Employee to ERPNext format."""
        data: dict[str, Any] = {
            "employee_name": entity.full_name,
            "company": self.company,
            "status": EMPLOYEE_STATUS_EXPORT_MAP.get(entity.status, "Active"),
            "date_of_joining": _format_date(entity.date_of_joining),
        }

        # Personal info
        if entity.person:
            data["first_name"] = entity.person.first_name or ""
            data["last_name"] = entity.person.last_name or ""
            if entity.person.email:
                data["company_email"] = entity.person.email

        if entity.gender:
            data["gender"] = GENDER_EXPORT_MAP.get(entity.gender, "Other")

        if entity.date_of_birth:
            data["date_of_birth"] = _format_date(entity.date_of_birth)

        if entity.personal_email:
            data["personal_email"] = entity.personal_email

        if entity.personal_phone:
            data["cell_number"] = entity.personal_phone

        # Organization structure (only if synced to ERPNext)
        if entity.department and entity.department.erpnext_id:
            data["department"] = entity.department.erpnext_id

        if entity.designation and entity.designation.erpnext_id:
            data["designation"] = entity.designation.erpnext_id

        if entity.employment_type and entity.employment_type.erpnext_id:
            data["employment_type"] = entity.employment_type.erpnext_id

        if entity.grade and entity.grade.erpnext_id:
            data["grade"] = entity.grade.erpnext_id

        # Reporting manager
        if entity.manager and entity.manager.erpnext_id:
            data["reports_to"] = entity.manager.erpnext_id

        # Bank details
        if entity.bank_name:
            data["bank_name"] = entity.bank_name
        if entity.bank_account_number:
            data["bank_ac_no"] = entity.bank_account_number

        # Employment dates
        if entity.date_of_leaving:
            data["relieving_date"] = _format_date(entity.date_of_leaving)

        if entity.probation_end_date:
            data["final_confirmation_date"] = _format_date(entity.probation_end_date)

        # Emergency contact
        if entity.emergency_contact_name:
            data["emergency_phone_number"] = entity.emergency_contact_phone or ""
            # ERPNext uses person_to_be_contacted for emergency contact

        return data

    def get_entity_id(self, entity: Employee) -> uuid.UUID:
        return entity.employee_id

    def get_erpnext_id(self, entity: Employee) -> Optional[str]:
        return entity.erpnext_id

    def set_erpnext_id(self, entity: Employee, erpnext_id: str) -> None:
        entity.erpnext_id = erpnext_id
        entity.last_synced_at = datetime.utcnow()


def _format_date(d: Optional[date]) -> Optional[str]:
    """Format date for ERPNext API (YYYY-MM-DD)."""
    if d:
        return d.isoformat()
    return None
