"""
HR Sync Services - ERPNext to DotMac ERP.

Sync services for HR entities:
- Department
- Designation
- Employment Type
- Employee Grade
- Employee
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.models.people.hr.employment_type import EmploymentType
from app.models.people.hr.employee_grade import EmployeeGrade
from app.models.people.hr.employee import Employee, EmployeeStatus, Gender
from app.models.person import Person
from app.services.erpnext.mappings.hr import (
    DepartmentMapping,
    DesignationMapping,
    EmploymentTypeMapping,
    EmployeeGradeMapping,
    EmployeeMapping,
)

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class DepartmentSyncService(BaseSyncService[Department]):
    """Sync Departments from ERPNext."""

    source_doctype = "Department"
    target_table = "hr.department"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = DepartmentMapping()
        self._department_cache: dict[str, Department] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch departments from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Department",
                since=since,
                fields=[
                    "name",
                    "department_name",
                    "parent_department",
                    "disabled",
                    "modified",
                ],
            )
        else:
            yield from client.get_departments()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext department to DotMac ERP format."""
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> Department:
        """Create Department entity."""
        parent_source_name = data.pop("_parent_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve parent department
        parent_id = None
        if parent_source_name:
            parent_id = self.resolve_parent_id(parent_source_name)

        department = Department(
            organization_id=self.organization_id,
            department_code=data["department_code"][:20],
            department_name=data["department_name"][:100],
            parent_department_id=parent_id,
            is_active=data.get("is_active", True),
            # Don't set created_by_id for synced records
        )
        return department

    def update_entity(self, entity: Department, data: dict[str, Any]) -> Department:
        """Update existing Department entity."""
        parent_source_name = data.pop("_parent_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.department_name = data["department_name"][:100]
        entity.is_active = data.get("is_active", True)

        # Resolve parent department
        if parent_source_name:
            entity.parent_department_id = self.resolve_parent_id(parent_source_name)

        # Don't set updated_by_id for synced records
        return entity

    def get_entity_id(self, entity: Department) -> uuid.UUID:
        return entity.department_id

    def find_existing_entity(self, source_name: str) -> Optional[Department]:
        """Find existing department by sync record or code."""
        if source_name in self._department_cache:
            return self._department_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            department = self.db.get(Department, sync_entity.target_id)
            if department:
                self._department_cache[source_name] = department
                return department

        return None


class DesignationSyncService(BaseSyncService[Designation]):
    """Sync Designations from ERPNext."""

    source_doctype = "Designation"
    target_table = "hr.designation"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = DesignationMapping()
        self._designation_cache: dict[str, Designation] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """Fetch designations from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Designation",
                since=since,
                fields=["name", "designation_name", "modified"],
            )
        else:
            yield from client.get_designations()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> Designation:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        designation = Designation(
            organization_id=self.organization_id,
            designation_code=data["designation_code"][:20],
            designation_name=data["designation_name"][:100],
            is_active=data.get("is_active", True),
            # created_by_id not set for synced records
        )
        return designation

    def update_entity(self, entity: Designation, data: dict[str, Any]) -> Designation:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.designation_name = data["designation_name"][:100]
        entity.is_active = data.get("is_active", True)
        # updated_by_id not set for synced records
        return entity

    def get_entity_id(self, entity: Designation) -> uuid.UUID:
        return entity.designation_id

    def find_existing_entity(self, source_name: str) -> Optional[Designation]:
        if source_name in self._designation_cache:
            return self._designation_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            designation = self.db.get(Designation, sync_entity.target_id)
            if designation:
                self._designation_cache[source_name] = designation
                return designation

        return None


class EmploymentTypeSyncService(BaseSyncService[EmploymentType]):
    """Sync Employment Types from ERPNext."""

    source_doctype = "Employment Type"
    target_table = "hr.employment_type"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = EmploymentTypeMapping()
        self._type_cache: dict[str, EmploymentType] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        if since:
            yield from client.get_modified_since(
                doctype="Employment Type",
                since=since,
                fields=["name", "modified"],
            )
        else:
            yield from client.get_employment_types()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> EmploymentType:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        emp_type = EmploymentType(
            organization_id=self.organization_id,
            type_code=data["type_code"][:20],
            type_name=data["type_name"][:100],
            is_active=data.get("is_active", True),
            # created_by_id not set for synced records
        )
        return emp_type

    def update_entity(self, entity: EmploymentType, data: dict[str, Any]) -> EmploymentType:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.type_name = data["type_name"][:100]
        entity.is_active = data.get("is_active", True)
        # updated_by_id not set for synced records
        return entity

    def get_entity_id(self, entity: EmploymentType) -> uuid.UUID:
        return entity.employment_type_id

    def find_existing_entity(self, source_name: str) -> Optional[EmploymentType]:
        if source_name in self._type_cache:
            return self._type_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            emp_type = self.db.get(EmploymentType, sync_entity.target_id)
            if emp_type:
                self._type_cache[source_name] = emp_type
                return emp_type

        return None


class EmployeeGradeSyncService(BaseSyncService[EmployeeGrade]):
    """Sync Employee Grades from ERPNext."""

    source_doctype = "Employee Grade"
    target_table = "hr.employee_grade"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = EmployeeGradeMapping()
        self._grade_cache: dict[str, EmployeeGrade] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        if since:
            yield from client.get_modified_since(
                doctype="Employee Grade",
                since=since,
                fields=["name", "default_base_pay", "modified"],
            )
        else:
            yield from client.get_employee_grades()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> EmployeeGrade:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        grade = EmployeeGrade(
            organization_id=self.organization_id,
            grade_code=data["grade_code"][:20],
            grade_name=data["grade_name"][:100],
            rank=data.get("rank", 0),
            min_salary=data.get("min_salary"),
            is_active=data.get("is_active", True),
            # created_by_id not set for synced records
        )
        return grade

    def update_entity(self, entity: EmployeeGrade, data: dict[str, Any]) -> EmployeeGrade:
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        entity.grade_name = data["grade_name"][:100]
        entity.rank = data.get("rank", 0)
        entity.min_salary = data.get("min_salary")
        entity.is_active = data.get("is_active", True)
        # updated_by_id not set for synced records
        return entity

    def get_entity_id(self, entity: EmployeeGrade) -> uuid.UUID:
        return entity.grade_id

    def find_existing_entity(self, source_name: str) -> Optional[EmployeeGrade]:
        if source_name in self._grade_cache:
            return self._grade_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            grade = self.db.get(EmployeeGrade, sync_entity.target_id)
            if grade:
                self._grade_cache[source_name] = grade
                return grade

        return None


class EmployeeSyncService(BaseSyncService[Employee]):
    """Sync Employees from ERPNext."""

    source_doctype = "Employee"
    target_table = "hr.employee"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = EmployeeMapping()
        self._employee_cache: dict[str, Employee] = {}
        self._person_cache: dict[str, Person] = {}

        # Caches for related entities (populated during sync)
        self._department_sync_cache: dict[str, uuid.UUID] = {}
        self._designation_sync_cache: dict[str, uuid.UUID] = {}
        self._employment_type_sync_cache: dict[str, uuid.UUID] = {}
        self._grade_sync_cache: dict[str, uuid.UUID] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        if since:
            yield from client.get_modified_since(
                doctype="Employee",
                since=since,
            )
        else:
            # Include inactive/left employees for historical expense claim linking
            yield from client.get_employees(include_inactive=True)

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._mapping.transform_record(record)

    def _resolve_entity_id(
        self,
        source_name: Optional[str],
        source_doctype: str,
        cache: dict[str, uuid.UUID],
    ) -> Optional[uuid.UUID]:
        """Resolve a foreign key ID from ERPNext source name."""
        if not source_name:
            return None

        if source_name in cache:
            return cache[source_name]

        # Lookup in sync entity table
        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            cache[source_name] = sync_entity.target_id
            return sync_entity.target_id

        return None

    def _find_or_create_person(self, data: dict[str, Any]) -> uuid.UUID:
        """Find or create a Person record for the employee.

        For employees without email, a placeholder email is generated using
        the employee code to satisfy the not-null constraint.

        Note: Each Employee must have a unique Person (1:1 relationship enforced
        by unique constraint on person_id). If multiple ERPNext employees share
        the same email, we create distinct Person records.
        """
        employee_code = data.get("employee_code", "")
        original_email = data.get("work_email") or data.get("personal_email") or data.get("_preferred_email")

        # Try to find existing Person that's not already linked to an Employee
        if original_email:
            # Check cache first - but our cache should track employee linkage
            cache_key = f"{employee_code}:{original_email}"
            if cache_key in self._person_cache:
                return self._person_cache[cache_key].id

            # Find Person by email that's NOT already linked to an Employee
            from app.models.people.hr.employee import Employee as EmployeeModel

            person = self.db.execute(
                select(Person)
                .outerjoin(EmployeeModel, EmployeeModel.person_id == Person.id)
                .where(
                    Person.email == original_email,
                    EmployeeModel.employee_id.is_(None),  # Not linked to any employee
                )
            ).scalar_one_or_none()

            if person:
                self._person_cache[cache_key] = person
                return person.id

        # Create new person with unique email
        first_name = data.get("first_name") or "Unknown"
        last_name = data.get("last_name") or ""
        employee_name = data.get("employee_name") or f"{first_name} {last_name}".strip()

        # Parse name if only employee_name provided
        if not first_name or first_name == "Unknown":
            name_parts = employee_name.split()
            if name_parts:
                first_name = name_parts[0]
                last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # For email: use original if available, otherwise generate from employee code
        # If original email is shared by multiple employees, create unique email
        if original_email:
            # Check if email is already in use by another Person linked to an Employee
            from app.models.people.hr.employee import Employee as EmployeeModel

            existing = self.db.execute(
                select(Person)
                .join(EmployeeModel, EmployeeModel.person_id == Person.id)
                .where(Person.email == original_email)
            ).scalar_one_or_none()

            if existing:
                # Email already used by another employee, create unique version
                email = f"{employee_code.lower()}+{original_email}" if employee_code else original_email
                logger.debug(f"Email {original_email} already in use, using {email}")
            else:
                email = original_email
        elif employee_code:
            email = f"{employee_code.lower()}@sync.internal"
        else:
            # Fallback using UUID to ensure uniqueness
            email = f"employee-{uuid.uuid4().hex[:8]}@sync.internal"

        logger.debug(f"Creating Person with email: {email}")

        person = Person(
            organization_id=self.organization_id,
            first_name=first_name[:100],
            last_name=last_name[:100] if last_name else None,
            email=email,
            phone=data.get("phone"),
            # created_by_id not set for synced records
        )
        self.db.add(person)
        self.db.flush()

        cache_key = f"{employee_code}:{original_email or email}"
        self._person_cache[cache_key] = person

        return person.id

    def create_entity(self, data: dict[str, Any]) -> Employee:
        # Extract and remove internal fields
        dept_source = data.pop("_department_source_name", None)
        desg_source = data.pop("_designation_source_name", None)
        type_source = data.pop("_employment_type_source_name", None)
        grade_source = data.pop("_grade_source_name", None)
        reports_source = data.pop("_reports_to_source_name", None)
        data.pop("_cost_center_source_name", None)
        data.pop("_payroll_cost_center_source_name", None)
        data.pop("_preferred_email", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve foreign keys
        department_id = self._resolve_entity_id(
            dept_source, "Department", self._department_sync_cache
        )
        designation_id = self._resolve_entity_id(
            desg_source, "Designation", self._designation_sync_cache
        )
        employment_type_id = self._resolve_entity_id(
            type_source, "Employment Type", self._employment_type_sync_cache
        )
        grade_id = self._resolve_entity_id(
            grade_source, "Employee Grade", self._grade_sync_cache
        )
        reports_to_id = self._resolve_entity_id(
            reports_source, "Employee", {}  # Don't cache manager lookups
        )

        # Find or create person
        person_id = self._find_or_create_person(data)

        # Map status
        status_str = data.get("status", "ACTIVE")
        try:
            status = EmployeeStatus(status_str)
        except ValueError:
            status = EmployeeStatus.ACTIVE

        # Map gender
        gender_str = data.get("gender")
        gender = None
        if gender_str:
            try:
                gender = Gender(gender_str)
            except ValueError:
                gender = Gender.PREFER_NOT_TO_SAY

        employee = Employee(
            organization_id=self.organization_id,
            person_id=person_id,
            employee_code=data["employee_code"][:20],
            department_id=department_id,
            designation_id=designation_id,
            employment_type_id=employment_type_id,
            grade_id=grade_id,
            reports_to_id=reports_to_id,
            date_of_birth=data.get("date_of_birth"),
            date_of_joining=data.get("date_of_joining"),
            date_of_leaving=data.get("date_of_leaving"),
            gender=gender,
            status=status,
            bank_name=data.get("bank_name"),
            bank_account_number=data.get("bank_account_number"),
            # created_by_id not set for synced records
        )
        return employee

    def update_entity(self, entity: Employee, data: dict[str, Any]) -> Employee:
        # Extract and remove internal fields
        dept_source = data.pop("_department_source_name", None)
        desg_source = data.pop("_designation_source_name", None)
        type_source = data.pop("_employment_type_source_name", None)
        grade_source = data.pop("_grade_source_name", None)
        reports_source = data.pop("_reports_to_source_name", None)
        data.pop("_cost_center_source_name", None)
        data.pop("_payroll_cost_center_source_name", None)
        data.pop("_preferred_email", None)
        data.pop("_source_modified", None)
        data.pop("_source_name", None)

        # Resolve foreign keys
        if dept_source:
            entity.department_id = self._resolve_entity_id(
                dept_source, "Department", self._department_sync_cache
            )
        if desg_source:
            entity.designation_id = self._resolve_entity_id(
                desg_source, "Designation", self._designation_sync_cache
            )
        if type_source:
            entity.employment_type_id = self._resolve_entity_id(
                type_source, "Employment Type", self._employment_type_sync_cache
            )
        if grade_source:
            entity.grade_id = self._resolve_entity_id(
                grade_source, "Employee Grade", self._grade_sync_cache
            )
        if reports_source:
            entity.reports_to_id = self._resolve_entity_id(
                reports_source, "Employee", {}
            )

        # Update fields
        entity.date_of_birth = data.get("date_of_birth") or entity.date_of_birth
        entity.date_of_joining = data.get("date_of_joining") or entity.date_of_joining
        entity.date_of_leaving = data.get("date_of_leaving")
        entity.bank_name = data.get("bank_name")
        entity.bank_account_number = data.get("bank_account_number")

        # Update status
        status_str = data.get("status", "ACTIVE")
        try:
            entity.status = EmployeeStatus(status_str)
        except ValueError:
            pass

        # updated_by_id not set for synced records
        return entity

    def get_entity_id(self, entity: Employee) -> uuid.UUID:
        return entity.employee_id

    def find_existing_entity(self, source_name: str) -> Optional[Employee]:
        if source_name in self._employee_cache:
            return self._employee_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            employee = self.db.get(Employee, sync_entity.target_id)
            if employee:
                self._employee_cache[source_name] = employee
                return employee

        return None
