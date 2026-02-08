"""Employee service - business logic for employee management.

This service encapsulates employee-related business logic:
- Employee CRUD operations
- Org chart / reporting hierarchy
- Status management (activate, terminate, etc.)
- Bulk operations

Routes should call this service and control the transaction boundary.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session, selectinload

from app.models.auth import AuthProvider, UserCredential
from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.location import Location
from app.models.people.attendance.shift_type import ShiftType
from app.models.people.hr import (
    Department,
    Designation,
    Employee,
    EmployeeGrade,
    EmployeeStatus,
    EmploymentType,
)
from app.models.person import Person
from app.services.audit_dispatcher import fire_audit_event
from app.services.auth_flow import hash_password
from app.services.common import PaginatedResult, PaginationParams, paginate

from .employee_types import (
    BulkResult,
    BulkUpdateData,
    EmployeeCreateData,
    EmployeeFilters,
    EmployeeSummary,
    EmployeeUpdateData,
    OrgChartNode,
    TerminationData,
)
from .errors import (
    EmployeeAlreadyExistsError,
    EmployeeNotFoundError,
    EmployeeStatusError,
    InvalidManagerError,
    ValidationError,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["EmployeeService"]


class EmployeeService:
    """Service for employee business logic.

    All methods that mutate data do NOT commit. The caller (route handler)
    is responsible for calling db.commit() after the operation succeeds.

    Args:
        db: SQLAlchemy database session.
        organization_id: The organization UUID for multi-tenant isolation.
        principal: Authenticated user/service token (for audit fields).
    """

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    # =========================================================================
    # Validation Helpers
    # =========================================================================

    def _validate_manager(
        self,
        employee_id: uuid.UUID,
        manager_id: uuid.UUID,
        visited: set | None = None,
    ) -> bool:
        """Check if setting manager_id would create a circular reference.

        Args:
            employee_id: The employee being modified.
            manager_id: The proposed manager ID.
            visited: Set of already visited employee IDs.

        Returns:
            True if the manager assignment is valid, False if it creates a cycle.
        """
        if visited is None:
            visited = set()

        # Can't report to self
        if employee_id == manager_id:
            return False

        # Check if manager reports to employee (directly or transitively)
        manager = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == manager_id,
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
        )
        if not manager:
            return True  # Manager doesn't exist, will fail separately

        visited.add(manager_id)

        if manager.reports_to_id is None:
            return True

        if manager.reports_to_id == employee_id:
            return False

        if manager.reports_to_id in visited:
            return False

        return self._validate_manager(employee_id, manager.reports_to_id, visited)

    def _generate_employee_code(self) -> str:
        """Generate a unique employee code.

        Uses sequence: EMP-YYYY-NNNN format.
        Delegates to SyncNumberingService for race-condition-safe generation.

        Returns:
            Generated employee code string.
        """
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        return SyncNumberingService(self.db).generate_next_number(
            self.organization_id, SequenceType.EMPLOYEE
        )

    def _validate_org_reference(
        self,
        model: type,
        entity_id: uuid.UUID | None,
        label: str,
    ) -> None:
        """Ensure a referenced entity exists within the organization."""
        if entity_id is None:
            return
        record = self.db.get(model, entity_id)
        if (
            not record
            or getattr(record, "organization_id", None) != self.organization_id
        ):
            raise ValidationError(f"{label} {entity_id} not found")
        if getattr(record, "is_deleted", False):
            raise ValidationError(f"{label} {entity_id} not found")

    # =========================================================================
    # Queries
    # =========================================================================

    def list_employees(
        self,
        filters: EmployeeFilters | None = None,
        pagination: PaginationParams | None = None,
        *,
        eager_load: bool = False,
    ) -> PaginatedResult[Employee]:
        """List employees with filters and pagination.

        Args:
            filters: Optional filter criteria.
            pagination: Pagination parameters (offset, limit).
            eager_load: If True, eager load person, department, and designation
                relationships to avoid N+1 queries. Use for web views.

        Returns:
            PaginatedResult containing employees and total count.
        """
        if filters is None:
            filters = EmployeeFilters()
        if pagination is None:
            pagination = PaginationParams()

        # Normalize status filter
        if isinstance(filters.status, str):
            status_value = filters.status.strip()
            if status_value:
                try:
                    filters.status = EmployeeStatus(status_value.upper())
                except ValueError:
                    filters.status = None
            else:
                filters.status = None

        stmt = select(Employee).where(
            Employee.organization_id == self.organization_id,
        )

        # Handle soft delete
        if not filters.include_deleted:
            stmt = stmt.where(Employee.is_deleted == False)

        if filters.is_active is not None and not filters.status:
            if filters.is_active:
                stmt = stmt.where(Employee.status == EmployeeStatus.ACTIVE)
            else:
                stmt = stmt.where(Employee.status != EmployeeStatus.ACTIVE)

        if filters.status:
            stmt = stmt.where(Employee.status == filters.status)

        if filters.department_id:
            stmt = stmt.where(Employee.department_id == filters.department_id)

        if filters.designation_id:
            stmt = stmt.where(Employee.designation_id == filters.designation_id)

        if filters.reports_to_id:
            stmt = stmt.where(Employee.reports_to_id == filters.reports_to_id)

        if filters.expense_approver_id:
            stmt = stmt.where(
                Employee.expense_approver_id == filters.expense_approver_id
            )

        if filters.search:
            search_term = f"%{filters.search}%"
            # Search by employee_code or join with Person for name/email
            stmt = stmt.join(Person, Employee.person_id == Person.id).where(
                or_(
                    Employee.employee_code.ilike(search_term),
                    Person.first_name.ilike(search_term),
                    Person.last_name.ilike(search_term),
                    Person.email.ilike(search_term),
                )
            )

        if filters.date_of_joining_from:
            stmt = stmt.where(Employee.date_of_joining >= filters.date_of_joining_from)

        if filters.date_of_joining_to:
            stmt = stmt.where(Employee.date_of_joining <= filters.date_of_joining_to)

        if filters.date_of_leaving_from:
            stmt = stmt.where(Employee.date_of_leaving >= filters.date_of_leaving_from)

        if filters.date_of_leaving_to:
            stmt = stmt.where(Employee.date_of_leaving <= filters.date_of_leaving_to)

        # Default ordering
        stmt = stmt.order_by(Employee.employee_code.asc())

        # Eager load relationships for web views
        if eager_load:
            stmt = stmt.options(
                selectinload(Employee.person),
                selectinload(Employee.department),
                selectinload(Employee.designation),
                selectinload(Employee.employment_type),
                selectinload(Employee.default_shift_type),
            )

        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=Employee.employee_id,
        )

    def get_employee_stats(self) -> dict:
        """Get employee count statistics by status.

        Returns:
            Dict with total, active, on_leave, and inactive counts.
        """
        stmt = (
            select(Employee.status, func.count(Employee.employee_id))
            .where(
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
            .group_by(Employee.status)
        )
        results = self.db.execute(stmt).all()

        status_counts = {status: count for status, count in results}

        return {
            "total": sum(status_counts.values()),
            "active": status_counts.get(EmployeeStatus.ACTIVE, 0),
            "on_leave": status_counts.get(EmployeeStatus.ON_LEAVE, 0),
            "inactive": (
                status_counts.get(EmployeeStatus.SUSPENDED, 0)
                + status_counts.get(EmployeeStatus.TERMINATED, 0)
                + status_counts.get(EmployeeStatus.RESIGNED, 0)
                + status_counts.get(EmployeeStatus.RETIRED, 0)
            ),
        }

    def get_employee(
        self, employee_id: uuid.UUID, include_deleted: bool = False
    ) -> Employee:
        """Get an employee by ID.

        Args:
            employee_id: The employee ID.
            include_deleted: Whether to include soft-deleted employees.

        Returns:
            The Employee object.

        Raises:
            EmployeeNotFoundError: If employee not found.
        """
        stmt = select(Employee).where(
            Employee.employee_id == employee_id,
            Employee.organization_id == self.organization_id,
        )

        if not include_deleted:
            stmt = stmt.where(Employee.is_deleted == False)

        employee = self.db.scalar(stmt)

        if not employee:
            raise EmployeeNotFoundError(employee_id)

        return employee

    def get_employee_by_code(self, employee_code: str) -> Employee | None:
        """Get an employee by employee code.

        Args:
            employee_code: The employee code.

        Returns:
            The Employee object or None if not found.
        """
        return self.db.scalar(
            select(Employee).where(
                Employee.employee_code == employee_code,
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
        )

    def get_employee_by_person(self, person_id: uuid.UUID) -> Employee | None:
        """Get an employee by their linked Person ID.

        Args:
            person_id: The Person ID.

        Returns:
            The Employee object or None if not found.
        """
        return self.db.scalar(
            select(Employee).where(
                Employee.person_id == person_id,
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
        )

    def search_employees(self, query: str, limit: int = 20) -> list[EmployeeSummary]:
        """Search employees for autocomplete.

        Args:
            query: Search query (name, email, or employee code).
            limit: Maximum number of results.

        Returns:
            List of EmployeeSummary objects.
        """
        search_term = f"%{query}%"

        stmt = (
            select(Employee, Person)
            .join(Person, Employee.person_id == Person.id)
            .where(
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
                or_(
                    Employee.employee_code.ilike(search_term),
                    Person.first_name.ilike(search_term),
                    Person.last_name.ilike(search_term),
                    Person.email.ilike(search_term),
                ),
            )
            .order_by(Person.first_name.asc())
            .limit(limit)
        )
        results = self.db.execute(stmt).all()

        return [
            EmployeeSummary(
                id=emp.employee_id,
                name=f"{person.first_name} {person.last_name}".strip(),
                email=person.email,
                employee_number=emp.employee_code,
                department=None,  # Would need to join with Department
                designation=None,  # Would need to join with Designation
                status=emp.status,
            )
            for emp, person in results
        ]

    def get_direct_reports(self, manager_id: uuid.UUID) -> list[Employee]:
        """Get all direct reports of a manager.

        Args:
            manager_id: The manager's employee ID.

        Returns:
            List of Employee objects who report to this manager.
        """
        stmt = (
            select(Employee)
            .where(
                Employee.reports_to_id == manager_id,
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
            .order_by(Employee.employee_code.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_org_chart(
        self, root_employee_id: uuid.UUID | None = None, depth: int = 3
    ) -> list[OrgChartNode]:
        """Get organization chart as a tree.

        Args:
            root_employee_id: Start from this employee. If None, starts from
                             employees with no manager.
            depth: Maximum depth to traverse.

        Returns:
            List of OrgChartNode objects with nested direct_reports.
        """

        def build_node(employee: Employee, current_depth: int) -> OrgChartNode:
            person = employee.person
            name = f"{person.first_name} {person.last_name}".strip() if person else ""
            email = person.email if person else None

            designation_name = (
                employee.designation.designation_name if employee.designation else None
            )
            department_name = (
                employee.department.department_name if employee.department else None
            )

            node = OrgChartNode(
                employee_id=employee.employee_id,
                name=name,
                designation=designation_name,
                department=department_name,
                email=email,
                direct_reports=[],
            )

            if current_depth < depth:
                reports = reports_by_manager.get(employee.employee_id, [])
                for report in reports:
                    node.direct_reports.append(build_node(report, current_depth + 1))

            return node

        reports_by_manager: dict[uuid.UUID | None, list[Employee]] = {}

        if root_employee_id:
            root_stmt = (
                select(Employee)
                .where(
                    Employee.employee_id == root_employee_id,
                    Employee.organization_id == self.organization_id,
                    Employee.is_deleted == False,
                )
                .options(
                    selectinload(Employee.person),
                    selectinload(Employee.department),
                    selectinload(Employee.designation),
                )
            )
            root = self.db.scalar(root_stmt)
            if not root:
                raise EmployeeNotFoundError(root_employee_id)

            employee_by_id = {root.employee_id: root}
            current_level = [root.employee_id]
            current_depth = 0

            while current_depth < depth and current_level:
                level_stmt = (
                    select(Employee)
                    .where(
                        Employee.reports_to_id.in_(current_level),
                        Employee.organization_id == self.organization_id,
                        Employee.is_deleted == False,
                    )
                    .options(
                        selectinload(Employee.person),
                        selectinload(Employee.department),
                        selectinload(Employee.designation),
                    )
                    .order_by(Employee.employee_code.asc())
                )
                level_employees = list(self.db.scalars(level_stmt).all())
                if not level_employees:
                    break
                for emp in level_employees:
                    employee_by_id[emp.employee_id] = emp
                    reports_by_manager.setdefault(emp.reports_to_id, []).append(emp)
                current_level = [emp.employee_id for emp in level_employees]
                current_depth += 1

            return [build_node(root, 0)]

        roots_stmt = (
            select(Employee)
            .where(
                Employee.reports_to_id.is_(None),
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
            .options(
                selectinload(Employee.person),
                selectinload(Employee.department),
                selectinload(Employee.designation),
            )
            .order_by(Employee.employee_code.asc())
        )
        roots = list(self.db.scalars(roots_stmt).all())

        for emp in roots:
            reports_by_manager.setdefault(None, []).append(emp)

        current_level = [emp.employee_id for emp in roots]
        current_depth = 0

        while current_depth < depth and current_level:
            level_stmt = (
                select(Employee)
                .where(
                    Employee.reports_to_id.in_(current_level),
                    Employee.organization_id == self.organization_id,
                    Employee.is_deleted == False,
                )
                .options(
                    selectinload(Employee.person),
                    selectinload(Employee.department),
                    selectinload(Employee.designation),
                )
                .order_by(Employee.employee_code.asc())
            )
            level_employees = list(self.db.scalars(level_stmt).all())
            if not level_employees:
                break
            for emp in level_employees:
                reports_by_manager.setdefault(emp.reports_to_id, []).append(emp)
            current_level = [emp.employee_id for emp in level_employees]
            current_depth += 1

        return [build_node(emp, 0) for emp in roots]

    # =========================================================================
    # CRUD
    # =========================================================================

    def create_employee(
        self,
        person_id: uuid.UUID,
        data: EmployeeCreateData,
    ) -> Employee:
        """Create a new employee.

        The employee is linked to an existing Person record. The Person
        contains contact information, while Employee contains HR-specific data.

        Args:
            person_id: The Person ID to link this employee to.
            data: Employee creation data.

        Returns:
            The created Employee (not yet committed).

        Raises:
            EmployeeAlreadyExistsError: If employee code already exists or
                                        person already has an employee record.
            ValidationError: If validation fails.
        """
        person = self.db.scalar(
            select(Person).where(
                Person.id == person_id,
                Person.organization_id == self.organization_id,
            )
        )
        if not person:
            raise ValidationError(
                f"Person {person_id} not found for organization {self.organization_id}"
            )

        # Check if person already has an employee record
        existing = self.get_employee_by_person(person_id)
        if existing:
            raise EmployeeAlreadyExistsError(
                str(person_id),
                f"Person {person_id} already has an employee record",
            )

        # Auto-generate employee code if not provided
        employee_code = data.employee_number
        if not employee_code:
            # Serialize code generation per org/year to avoid duplicates.
            lock_key = (self.organization_id.int ^ datetime.now(UTC).year) % (2**63)
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": lock_key},
            )
            employee_code = self._generate_employee_code()

        # Check for duplicate employee code
        existing = self.get_employee_by_code(employee_code)
        if existing:
            raise EmployeeAlreadyExistsError(
                employee_code,
                f"Employee with code '{employee_code}' already exists",
            )

        # Validate manager doesn't create cycle (not possible for new employee)
        if data.reports_to_id:
            manager = self.db.scalar(
                select(Employee).where(
                    Employee.employee_id == data.reports_to_id,
                    Employee.organization_id == self.organization_id,
                    Employee.is_deleted == False,
                )
            )
            if not manager:
                raise ValidationError(f"Manager with ID {data.reports_to_id} not found")

        if data.expense_approver_id:
            approver = self.db.scalar(
                select(Employee).where(
                    Employee.employee_id == data.expense_approver_id,
                    Employee.organization_id == self.organization_id,
                    Employee.is_deleted == False,
                )
            )
            if not approver:
                raise ValidationError(
                    f"Expense approver with ID {data.expense_approver_id} not found"
                )

        self._validate_org_reference(Department, data.department_id, "Department")
        self._validate_org_reference(Designation, data.designation_id, "Designation")
        self._validate_org_reference(
            EmploymentType, data.employment_type_id, "Employment type"
        )
        self._validate_org_reference(EmployeeGrade, data.grade_id, "Employee grade")
        self._validate_org_reference(CostCenter, data.cost_center_id, "Cost center")
        self._validate_org_reference(Location, data.assigned_location_id, "Location")
        self._validate_org_reference(
            ShiftType, data.default_shift_type_id, "Shift type"
        )

        employee = Employee(
            organization_id=self.organization_id,
            person_id=person_id,
            employee_code=employee_code,
            department_id=data.department_id,
            designation_id=data.designation_id,
            employment_type_id=data.employment_type_id,
            grade_id=data.grade_id,
            reports_to_id=data.reports_to_id,
            expense_approver_id=data.expense_approver_id,
            assigned_location_id=data.assigned_location_id,
            default_shift_type_id=data.default_shift_type_id,
            date_of_joining=data.date_of_joining or date.today(),
            probation_end_date=data.probation_end_date,
            confirmation_date=data.confirmation_date,
            status=data.status or EmployeeStatus.DRAFT,
            cost_center_id=data.cost_center_id,
            # Personal contact
            personal_email=data.personal_email,
            personal_phone=data.personal_phone,
            # Emergency contact
            emergency_contact_name=data.emergency_contact_name,
            emergency_contact_phone=data.emergency_contact_phone,
            # Bank details
            bank_name=data.bank_name,
            bank_account_number=data.bank_account_number,
            bank_account_name=data.bank_account_name,
            bank_branch_code=data.bank_sort_code,
            ctc=data.ctc,
            salary_mode=data.salary_mode,
            notes=data.notes,
            created_by_id=self.principal.id if self.principal else None,
        )

        self.db.add(employee)
        self.db.flush()

        fire_audit_event(
            db=self.db,
            organization_id=employee.organization_id,
            table_schema="hr",
            table_name="employee",
            record_id=str(employee.employee_id),
            action=AuditAction.INSERT,
            new_values={
                "employee_number": employee.employee_code,
                "person_id": str(employee.person_id),
            },
        )

        return employee

    def update_employee(
        self, employee_id: uuid.UUID, data: EmployeeUpdateData
    ) -> Employee:
        """Update an existing employee.

        Args:
            employee_id: The employee ID.
            data: Fields to update.

        Returns:
            The updated Employee (not yet committed).

        Raises:
            EmployeeNotFoundError: If employee not found.
            EmployeeAlreadyExistsError: If employee code conflicts.
            InvalidManagerError: If manager assignment creates cycle.
        """
        employee = self.get_employee(employee_id)

        provided_fields: set[str] = set(getattr(data, "provided_fields", set()))
        use_provided_fields = bool(provided_fields)

        # Validate and update employee code
        if (
            data.employee_number is not None
            and data.employee_number != employee.employee_code
        ):
            existing = self.get_employee_by_code(data.employee_number)
            if existing and existing.employee_id != employee_id:
                raise EmployeeAlreadyExistsError(
                    data.employee_number,
                    f"Employee with code '{data.employee_number}' already exists",
                )
            employee.employee_code = data.employee_number

        # Validate manager doesn't create cycle
        if (
            data.reports_to_id is not None
            and data.reports_to_id != employee.reports_to_id
        ):
            if data.reports_to_id:
                manager = self.db.scalar(
                    select(Employee).where(
                        Employee.employee_id == data.reports_to_id,
                        Employee.organization_id == self.organization_id,
                        Employee.is_deleted == False,
                    )
                )
                if not manager:
                    raise ValidationError(
                        f"Manager with ID {data.reports_to_id} not found"
                    )
                if not self._validate_manager(employee_id, data.reports_to_id):
                    raise InvalidManagerError()
            employee.reports_to_id = data.reports_to_id
        elif use_provided_fields and "reports_to_id" in provided_fields:
            employee.reports_to_id = None

        if (
            data.expense_approver_id is not None
            and data.expense_approver_id != employee.expense_approver_id
        ):
            if data.expense_approver_id == employee_id:
                raise ValidationError("Expense approver cannot be the employee")
            if data.expense_approver_id:
                approver = self.db.scalar(
                    select(Employee).where(
                        Employee.employee_id == data.expense_approver_id,
                        Employee.organization_id == self.organization_id,
                        Employee.is_deleted == False,
                    )
                )
                if not approver:
                    raise ValidationError(
                        f"Expense approver with ID {data.expense_approver_id} not found"
                    )
            employee.expense_approver_id = data.expense_approver_id
        elif use_provided_fields and "expense_approver_id" in provided_fields:
            employee.expense_approver_id = None

        # Update department
        if data.department_id is not None:
            self._validate_org_reference(Department, data.department_id, "Department")
            employee.department_id = data.department_id
        elif use_provided_fields and "department_id" in provided_fields:
            employee.department_id = None

        # Update designation
        if data.designation_id is not None:
            self._validate_org_reference(
                Designation, data.designation_id, "Designation"
            )
            employee.designation_id = data.designation_id
        elif use_provided_fields and "designation_id" in provided_fields:
            employee.designation_id = None

        if data.employment_type_id is not None:
            self._validate_org_reference(
                EmploymentType, data.employment_type_id, "Employment type"
            )
            employee.employment_type_id = data.employment_type_id
        elif use_provided_fields and "employment_type_id" in provided_fields:
            employee.employment_type_id = None

        if data.grade_id is not None:
            self._validate_org_reference(EmployeeGrade, data.grade_id, "Employee grade")
            employee.grade_id = data.grade_id
        elif use_provided_fields and "grade_id" in provided_fields:
            employee.grade_id = None

        if data.cost_center_id is not None:
            self._validate_org_reference(CostCenter, data.cost_center_id, "Cost center")
            employee.cost_center_id = data.cost_center_id
        elif use_provided_fields and "cost_center_id" in provided_fields:
            employee.cost_center_id = None

        if data.assigned_location_id is not None:
            self._validate_org_reference(
                Location, data.assigned_location_id, "Location"
            )
            employee.assigned_location_id = data.assigned_location_id
        elif use_provided_fields and "assigned_location_id" in provided_fields:
            employee.assigned_location_id = None

        if data.default_shift_type_id is not None:
            self._validate_org_reference(
                ShiftType, data.default_shift_type_id, "Shift type"
            )
            employee.default_shift_type_id = data.default_shift_type_id
        elif use_provided_fields and "default_shift_type_id" in provided_fields:
            employee.default_shift_type_id = None

        # Update simple fields
        if data.date_of_joining is not None:
            employee.date_of_joining = data.date_of_joining

        if data.date_of_leaving is not None:
            employee.date_of_leaving = data.date_of_leaving

        if data.status is not None:
            employee.status = data.status

        if data.probation_end_date is not None:
            employee.probation_end_date = data.probation_end_date
        elif use_provided_fields and "probation_end_date" in provided_fields:
            employee.probation_end_date = None

        if data.confirmation_date is not None:
            employee.confirmation_date = data.confirmation_date
        elif use_provided_fields and "confirmation_date" in provided_fields:
            employee.confirmation_date = None

        # Bank details
        if data.bank_name is not None:
            employee.bank_name = data.bank_name
        elif use_provided_fields and "bank_name" in provided_fields:
            employee.bank_name = None
        if data.bank_account_number is not None:
            employee.bank_account_number = data.bank_account_number
        elif use_provided_fields and "bank_account_number" in provided_fields:
            employee.bank_account_number = None
        if data.bank_account_name is not None:
            employee.bank_account_name = data.bank_account_name
        elif use_provided_fields and "bank_account_name" in provided_fields:
            employee.bank_account_name = None
        if data.bank_sort_code is not None:
            employee.bank_branch_code = data.bank_sort_code
        elif use_provided_fields and "bank_sort_code" in provided_fields:
            employee.bank_branch_code = None
        if data.ctc is not None:
            employee.ctc = data.ctc
        elif use_provided_fields and "ctc" in provided_fields:
            employee.ctc = None
        if data.salary_mode is not None:
            employee.salary_mode = data.salary_mode
        elif use_provided_fields and "salary_mode" in provided_fields:
            employee.salary_mode = None

        # Personal contact
        if data.personal_email is not None:
            employee.personal_email = data.personal_email
        elif use_provided_fields and "personal_email" in provided_fields:
            employee.personal_email = None
        if data.personal_phone is not None:
            employee.personal_phone = data.personal_phone
        elif use_provided_fields and "personal_phone" in provided_fields:
            employee.personal_phone = None

        # Emergency contact
        if data.emergency_contact_name is not None:
            employee.emergency_contact_name = data.emergency_contact_name
        elif use_provided_fields and "emergency_contact_name" in provided_fields:
            employee.emergency_contact_name = None
        if data.emergency_contact_phone is not None:
            employee.emergency_contact_phone = data.emergency_contact_phone
        elif use_provided_fields and "emergency_contact_phone" in provided_fields:
            employee.emergency_contact_phone = None

        if data.notes is not None:
            employee.notes = data.notes
        elif use_provided_fields and "notes" in provided_fields:
            employee.notes = None

        employee.updated_at = datetime.now(UTC)
        employee.updated_by_id = self.principal.id if self.principal else None
        employee.version += 1

        fire_audit_event(
            db=self.db,
            organization_id=employee.organization_id,
            table_schema="hr",
            table_name="employee",
            record_id=str(employee.employee_id),
            action=AuditAction.UPDATE,
            new_values={"updated_fields": "employee_data"},
        )

        return employee

    # =========================================================================
    # User Linking / Credentials
    # =========================================================================

    def link_employee_to_person(
        self,
        employee_id: uuid.UUID,
        person_id: uuid.UUID,
    ) -> Employee:
        """Link an employee record to an existing Person (user)."""
        employee = self.get_employee(employee_id)

        person = self.db.get(Person, person_id)
        if not person or person.organization_id != self.organization_id:
            raise ValidationError(f"Person {person_id} not found for organization")

        existing = self.get_employee_by_person(person_id)
        if existing and existing.employee_id != employee.employee_id:
            raise ValidationError("Person is already linked to another employee")

        employee.person_id = person_id
        self.db.flush()
        return employee

    def create_user_credentials_for_employee(
        self,
        employee_id: uuid.UUID,
        *,
        username: str | None,
        password: str | None,
        provider: AuthProvider = AuthProvider.local,
        must_change_password: bool = True,
    ) -> UserCredential:
        """Create user credentials for an employee's linked Person."""
        employee = self.get_employee(employee_id)
        person = self.db.get(Person, employee.person_id)
        if not person or person.organization_id != self.organization_id:
            raise ValidationError("Employee is not linked to a valid user")

        if provider == AuthProvider.local and (not username or not password):
            raise ValidationError("Username and password are required for local auth")

        existing = (
            self.db.query(UserCredential)
            .filter(UserCredential.person_id == person.id)
            .filter(UserCredential.provider == provider)
            .first()
        )
        if existing:
            raise ValidationError("User credentials already exist for this employee")

        if username:
            username_in_use = (
                self.db.query(UserCredential)
                .filter(UserCredential.provider == provider)
                .filter(UserCredential.username == username)
                .first()
            )
            if username_in_use:
                raise ValidationError("Username is already in use")

        password_hash = hash_password(password) if password else None
        credential = UserCredential(
            person_id=person.id,
            provider=provider,
            username=username,
            password_hash=password_hash,
            must_change_password=must_change_password,
            password_updated_at=datetime.now(UTC) if password_hash else None,
        )
        self.db.add(credential)
        self.db.flush()
        return credential

    def delete_employee(self, employee_id: uuid.UUID) -> None:
        """Soft delete an employee.

        Args:
            employee_id: The employee ID.

        Raises:
            EmployeeNotFoundError: If employee not found.
        """
        employee = self.get_employee(employee_id)

        employee.is_deleted = True
        employee.deleted_at = datetime.now(UTC)
        employee.deleted_by_id = self.principal.id if self.principal else None
        employee.updated_at = datetime.now(UTC)

    # =========================================================================
    # Status Management
    # =========================================================================

    def activate_employee(self, employee_id: uuid.UUID) -> Employee:
        """Activate an employee.

        Args:
            employee_id: The employee ID.

        Returns:
            The updated Employee.

        Raises:
            EmployeeNotFoundError: If employee not found.
        """
        employee = self.get_employee(employee_id)
        employee.status = EmployeeStatus.ACTIVE
        employee.updated_at = datetime.now(UTC)
        employee.updated_by_id = self.principal.id if self.principal else None
        return employee

    def suspend_employee(
        self, employee_id: uuid.UUID, reason: str | None = None
    ) -> Employee:
        """Suspend an employee.

        Args:
            employee_id: The employee ID.
            reason: Optional reason for suspension.

        Returns:
            The updated Employee.

        Raises:
            EmployeeNotFoundError: If employee not found.
        """
        employee = self.get_employee(employee_id)
        employee.status = EmployeeStatus.SUSPENDED
        employee.updated_at = datetime.now(UTC)
        employee.updated_by_id = self.principal.id if self.principal else None
        # Note: reason could be stored in notes field or separate audit log
        return employee

    def terminate_employee(
        self, employee_id: uuid.UUID, data: TerminationData
    ) -> Employee:
        """Terminate an employee.

        Args:
            employee_id: The employee ID.
            data: Termination data.

        Returns:
            The updated Employee.

        Raises:
            EmployeeNotFoundError: If employee not found.
            EmployeeStatusError: If employee is already terminated.
        """
        employee = self.get_employee(employee_id)

        if employee.status == EmployeeStatus.TERMINATED:
            raise EmployeeStatusError(
                employee.status.value,
                "Employee is already terminated",
            )

        old_status = employee.status.value if employee.status else None

        employee.status = EmployeeStatus.TERMINATED
        employee.date_of_leaving = data.date_of_leaving
        employee.updated_at = datetime.now(UTC)
        employee.updated_by_id = self.principal.id if self.principal else None

        fire_audit_event(
            db=self.db,
            organization_id=employee.organization_id,
            table_schema="hr",
            table_name="employee",
            record_id=str(employee.employee_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "TERMINATED"},
            reason=data.reason if hasattr(data, "reason") else None,
        )

        return employee

    def resign_employee(
        self, employee_id: uuid.UUID, date_of_leaving: date
    ) -> Employee:
        """Record employee resignation.

        Args:
            employee_id: The employee ID.
            date_of_leaving: The last working day.

        Returns:
            The updated Employee.

        Raises:
            EmployeeNotFoundError: If employee not found.
        """
        employee = self.get_employee(employee_id)
        employee.status = EmployeeStatus.RESIGNED
        employee.date_of_leaving = date_of_leaving
        employee.updated_at = datetime.now(UTC)
        employee.updated_by_id = self.principal.id if self.principal else None
        return employee

    def set_on_leave(self, employee_id: uuid.UUID) -> Employee:
        """Set employee status to on leave.

        Args:
            employee_id: The employee ID.

        Returns:
            The updated Employee.

        Raises:
            EmployeeNotFoundError: If employee not found.
            EmployeeStatusError: If employee is terminated.
        """
        employee = self.get_employee(employee_id)

        if employee.status == EmployeeStatus.TERMINATED:
            raise EmployeeStatusError(
                employee.status.value,
                "Cannot set terminated employee on leave",
            )

        employee.status = EmployeeStatus.ON_LEAVE
        employee.updated_at = datetime.now(UTC)
        employee.updated_by_id = self.principal.id if self.principal else None

        return employee

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def bulk_update(self, data: BulkUpdateData) -> BulkResult:
        """Bulk update multiple employees.

        Args:
            data: Bulk update data containing IDs and fields to update.

        Returns:
            BulkResult with count of updated employees and any failures.
        """
        if not data.ids:
            return BulkResult()

        result = BulkResult()
        now = datetime.now(UTC)

        # Build update dict from non-None fields
        updates: dict = {}
        if data.department_id is not None:
            self._validate_org_reference(Department, data.department_id, "Department")
            updates["department_id"] = data.department_id
        if data.designation_id is not None:
            self._validate_org_reference(
                Designation, data.designation_id, "Designation"
            )
            updates["designation_id"] = data.designation_id
        if data.status is not None:
            updates["status"] = data.status
        if data.reports_to_id is not None:
            manager = self.db.scalar(
                select(Employee).where(
                    Employee.employee_id == data.reports_to_id,
                    Employee.organization_id == self.organization_id,
                    Employee.is_deleted == False,
                )
            )
            if not manager:
                raise ValidationError(f"Manager with ID {data.reports_to_id} not found")

            for employee_id in data.ids:
                if not self._validate_manager(employee_id, data.reports_to_id):
                    raise InvalidManagerError()
            updates["reports_to_id"] = data.reports_to_id

        if not updates:
            return result

        # Add audit fields
        updates["updated_at"] = now
        updates["version"] = Employee.version + 1
        if self.principal:
            updates["updated_by_id"] = self.principal.id

        # Perform bulk update
        stmt = (
            update(Employee)
            .where(
                Employee.employee_id.in_(data.ids),
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
            .values(**updates)
        )
        result_proxy = self.db.execute(stmt)
        result.updated_count = result_proxy.rowcount

        return result

    def bulk_delete(self, ids: list[uuid.UUID]) -> BulkResult:
        """Bulk soft-delete multiple employees.

        Args:
            ids: List of employee IDs to delete.

        Returns:
            BulkResult with count of deleted employees.
        """
        if not ids:
            return BulkResult()

        result = BulkResult()
        now = datetime.now(UTC)
        user_id = self.principal.id if self.principal else None

        stmt = (
            update(Employee)
            .where(
                Employee.employee_id.in_(ids),
                Employee.organization_id == self.organization_id,
                Employee.is_deleted == False,
            )
            .values(
                is_deleted=True,
                deleted_at=now,
                deleted_by_id=user_id,
                updated_at=now,
            )
        )
        result_proxy = self.db.execute(stmt)
        result.deleted_count = result_proxy.rowcount

        return result
