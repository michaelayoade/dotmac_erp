"""Workforce / department / company / person directory views for CRM.

Extracted from the former monolithic dotmac_crm_sync_service.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload


if TYPE_CHECKING:
    from app.models.finance.ap.supplier import Supplier  # noqa: F401

from app.models.people.hr.employee import Employee
from app.schemas.sync.dotmac_crm import (
    CompanyContactRead,
    CompanyListResponse,
    DepartmentListResponse,
    DepartmentMemberRead,
    DepartmentRead,
    PersonContactRead,
    PersonListResponse,
    WorkforceEmployeeListResponse,
    WorkforceEmployeeRead,
)

# CRM → ERP translation policy lives in crm_mappings (pure, side-effect-free).
# Re-imported here so the canonical import sites
# (`from ...dotmac_crm_sync_service import PROJECT_STATUS_MAP`) and the in-class
# references keep resolving against this module's namespace.

from app.services.sync.crm.base import _CRMSyncBase

logger = logging.getLogger(__name__)


class _DirectoryMixin(_CRMSyncBase):
    def list_workforce_employees(
        self,
        org_id: UUID,
        *,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> WorkforceEmployeeListResponse:
        """List employees for CRM author/staff lookup."""
        from app.models.people.hr.employee import EmployeeStatus

        stmt = (
            select(Employee)
            .options(
                joinedload(Employee.person),
                joinedload(Employee.department),
                joinedload(Employee.designation),
            )
            .where(Employee.organization_id == org_id)
        )
        if not include_inactive:
            stmt = stmt.where(Employee.status == EmployeeStatus.ACTIVE)

        # Exclude rows without a person/email because CRM needs email key for mapping.
        employees_all = list(
            self.db.scalars(stmt.order_by(Employee.employee_code)).all()
        )
        employees_filtered = [
            emp
            for emp in employees_all
            if emp.person and (emp.person.email or "").strip()
        ]

        total = len(employees_filtered)
        page = employees_filtered[offset : offset + limit]
        has_more = offset + limit < total

        rows = [
            WorkforceEmployeeRead(
                employee_id=emp.employee_id,
                email=emp.person.email.strip().lower()
                if emp.person and emp.person.email
                else "",
                is_active=emp.status == EmployeeStatus.ACTIVE,
                full_name=(
                    f"{emp.person.first_name or ''} {emp.person.last_name or ''}".strip()
                    if emp.person
                    else None
                ),
                department=emp.department.department_name if emp.department else None,
                designation=emp.designation.designation_name
                if emp.designation
                else None,
            )
            for emp in page
        ]

        return WorkforceEmployeeListResponse(
            employees=rows,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def list_departments(
        self,
        org_id: UUID,
        *,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> DepartmentListResponse:
        """List departments with members for CRM service-team mapping."""
        from app.models.people.hr.department import Department
        from app.models.people.hr.employee import EmployeeStatus

        stmt = select(Department).where(Department.organization_id == org_id)
        if not include_inactive:
            stmt = stmt.where(Department.is_active.is_(True))

        # Count total before pagination
        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Department.department_id).subquery()
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = (
            stmt.options(
                selectinload(Department.head).joinedload(Employee.person),
                selectinload(Department.head).joinedload(Employee.designation),
                selectinload(Department.employees).joinedload(Employee.person),
                selectinload(Department.employees).joinedload(Employee.designation),
            )
            .order_by(Department.department_name)
            .offset(offset)
            .limit(limit)
        )
        departments = list(self.db.scalars(stmt).unique().all())

        result: list[DepartmentRead] = []
        for dept in departments:
            # Build manager from head relationship
            manager = None
            if dept.head and dept.head.person:
                p = dept.head.person
                head_designation = dept.head.designation
                manager = DepartmentMemberRead(
                    employee_id=dept.head.employee_id,
                    email=p.email,
                    full_name=f"{p.first_name} {p.last_name}".strip(),
                    designation_name=head_designation.designation_name
                    if head_designation
                    else None,
                    designation_id=head_designation.designation_id
                    if head_designation
                    else None,
                    role="manager",
                    is_active=dept.head.status == EmployeeStatus.ACTIVE,
                )

            # Build members from employees relationship
            members: list[DepartmentMemberRead] = []
            for emp in dept.employees:
                if not include_inactive and emp.status != EmployeeStatus.ACTIVE:
                    continue
                if emp.person:
                    ep = emp.person
                    emp_designation = emp.designation
                    members.append(
                        DepartmentMemberRead(
                            employee_id=emp.employee_id,
                            email=ep.email,
                            full_name=f"{ep.first_name} {ep.last_name}".strip(),
                            designation_name=emp_designation.designation_name
                            if emp_designation
                            else None,
                            designation_id=emp_designation.designation_id
                            if emp_designation
                            else None,
                            role=None,
                            is_active=emp.status == EmployeeStatus.ACTIVE,
                        )
                    )

            result.append(
                DepartmentRead(
                    department_id=dept.department_code,
                    department_name=dept.department_name,
                    department_type="operations",
                    manager=manager,
                    members=members,
                )
            )

        return DepartmentListResponse(
            departments=result,
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_companies(
        self,
        org_id: UUID,
        *,
        updated_since: datetime | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> CompanyListResponse:
        """List company/government customers for CRM contacts sync."""
        from app.models.finance.ar.customer import Customer, CustomerType

        stmt = select(Customer).where(
            Customer.organization_id == org_id,
            Customer.customer_type.in_([CustomerType.COMPANY, CustomerType.GOVERNMENT]),
        )
        if not include_inactive:
            stmt = stmt.where(Customer.is_active.is_(True))
        if updated_since:
            stmt = stmt.where(
                func.coalesce(Customer.updated_at, Customer.created_at) >= updated_since
            )

        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Customer.customer_id).subquery()
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = stmt.order_by(Customer.legal_name).offset(offset).limit(limit + 1)
        customers = list(self.db.scalars(stmt).all())
        has_more = len(customers) > limit
        if has_more:
            customers = customers[:limit]

        companies = [
            CompanyContactRead(
                customer_id=c.customer_id,
                customer_code=c.customer_code,
                legal_name=c.legal_name,
                tax_id=c.tax_identification_number,
                billing_address=c.billing_address,
                primary_contact=c.primary_contact,
                crm_id=c.crm_id,
            )
            for c in customers
        ]

        return CompanyListResponse(
            companies=companies,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def list_people_contacts(
        self,
        org_id: UUID,
        *,
        updated_since: datetime | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> PersonListResponse:
        """List individual customers as person contacts for CRM sync."""
        from app.models.finance.ar.customer import Customer, CustomerType

        stmt = select(Customer).where(
            Customer.organization_id == org_id,
            Customer.customer_type == CustomerType.INDIVIDUAL,
        )
        if not include_inactive:
            stmt = stmt.where(Customer.is_active.is_(True))
        if updated_since:
            stmt = stmt.where(
                func.coalesce(Customer.updated_at, Customer.created_at) >= updated_since
            )

        count_stmt = select(func.count()).select_from(
            stmt.with_only_columns(Customer.customer_id).subquery()
        )
        total = self.db.scalar(count_stmt) or 0

        stmt = stmt.order_by(Customer.legal_name).offset(offset).limit(limit + 1)
        customers = list(self.db.scalars(stmt).all())
        has_more = len(customers) > limit
        if has_more:
            customers = customers[:limit]

        contacts: list[PersonContactRead] = []
        for c in customers:
            # Extract email/phone from primary_contact JSONB
            email = None
            phone = None
            if c.primary_contact and isinstance(c.primary_contact, dict):
                email = c.primary_contact.get("email")
                phone = c.primary_contact.get("phone")

            contacts.append(
                PersonContactRead(
                    contact_id=c.customer_id,
                    customer_code=c.customer_code,
                    legal_name=c.legal_name,
                    email=email,
                    phone=phone,
                    crm_id=c.crm_id,
                )
            )

        return PersonListResponse(
            contacts=contacts,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )
