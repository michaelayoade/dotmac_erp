"""
Staging Import Service - Import validated staging data to production.

Moves data from staging tables to production after validation passes.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.models.people.hr.employment_type import EmploymentType
from app.models.people.hr.employee_grade import EmployeeGrade
from app.models.people.hr.employee import Employee, EmployeeStatus, Gender
from app.models.sync import SyncEntity, SyncStatus
from app.models.sync.staging import (
    StagingDepartment,
    StagingDesignation,
    StagingEmployee,
    StagingEmployeeGrade,
    StagingEmploymentType,
    StagingStatus,
    StagingSyncBatch,
)

logger = logging.getLogger(__name__)


class ImportResult:
    """Result of an import operation."""

    def __init__(self):
        self.total = 0
        self.imported = 0
        self.skipped = 0
        self.errors: list[str] = []

    def add_error(self, message: str):
        self.errors.append(message)


class StagingImportService:
    """
    Imports validated staging data to production tables.

    Import order (respects foreign key dependencies):
    1. Departments (may have parent references)
    2. Designations
    3. Employment Types
    4. Employee Grades
    5. Employees (references all above + Person)
    """

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.user_id = user_id

        # Caches for resolved IDs (source_name -> production UUID)
        self._dept_cache: dict[str, uuid.UUID] = {}
        self._desg_cache: dict[str, uuid.UUID] = {}
        self._emptype_cache: dict[str, uuid.UUID] = {}
        self._grade_cache: dict[str, uuid.UUID] = {}
        self._emp_cache: dict[str, uuid.UUID] = {}

    def import_batch(
        self,
        batch_id: uuid.UUID,
        skip_invalid: bool = False,
        generate_placeholder_emails: bool = True,
    ) -> dict[str, ImportResult]:
        """
        Import all validated records from a staging batch.

        Args:
            batch_id: The batch to import
            skip_invalid: If True, skip invalid records; if False, abort on invalid
            generate_placeholder_emails: Generate emails for employees without one

        Returns:
            Dict of ImportResult by entity type
        """
        batch = self.db.get(StagingSyncBatch, batch_id)
        if not batch:
            raise ValueError(f"Batch not found: {batch_id}")

        if batch.status not in ("VALIDATED", "SYNCED"):
            raise ValueError(f"Batch must be validated before import. Status: {batch.status}")

        # Check for invalid records
        invalid_count = self._count_invalid_records(batch_id)
        if invalid_count > 0 and not skip_invalid:
            raise ValueError(
                f"Batch has {invalid_count} invalid records. "
                "Fix errors or use skip_invalid=True"
            )

        logger.info(f"Starting import for batch {batch_id}")
        batch.status = "IMPORTING"
        self.db.flush()

        results = {}

        try:
            # Pre-populate caches with existing production data
            self._populate_caches()

            # Import in dependency order
            results["departments"] = self._import_departments(batch_id)
            results["designations"] = self._import_designations(batch_id)
            results["employment_types"] = self._import_employment_types(batch_id)
            results["employee_grades"] = self._import_employee_grades(batch_id)
            results["employees"] = self._import_employees(
                batch_id,
                generate_placeholder_emails=generate_placeholder_emails,
            )

            # Update batch status
            total_imported = sum(r.imported for r in results.values())
            total_errors = sum(len(r.errors) for r in results.values())

            batch.status = "IMPORTED" if total_errors == 0 else "COMPLETED_WITH_ERRORS"
            batch.imported_at = datetime.utcnow()
            batch.imported_records = total_imported

            self.db.commit()
            logger.info(f"Import complete: {total_imported} records imported")

        except Exception as e:
            logger.exception(f"Import failed: {e}")
            batch.status = "FAILED"
            batch.notes = str(e)
            self.db.commit()
            raise

        return results

    def _count_invalid_records(self, batch_id: uuid.UUID) -> int:
        """Count invalid records in batch."""
        count = 0
        count += self.db.query(StagingDepartment).filter(
            StagingDepartment.batch_id == batch_id,
            StagingDepartment.validation_status == StagingStatus.INVALID,
        ).count()
        count += self.db.query(StagingDesignation).filter(
            StagingDesignation.batch_id == batch_id,
            StagingDesignation.validation_status == StagingStatus.INVALID,
        ).count()
        count += self.db.query(StagingEmploymentType).filter(
            StagingEmploymentType.batch_id == batch_id,
            StagingEmploymentType.validation_status == StagingStatus.INVALID,
        ).count()
        count += self.db.query(StagingEmployeeGrade).filter(
            StagingEmployeeGrade.batch_id == batch_id,
            StagingEmployeeGrade.validation_status == StagingStatus.INVALID,
        ).count()
        count += self.db.query(StagingEmployee).filter(
            StagingEmployee.batch_id == batch_id,
            StagingEmployee.validation_status == StagingStatus.INVALID,
        ).count()
        return count

    def _populate_caches(self):
        """Pre-populate caches with existing production data."""
        # Load existing sync entities to map source names to production IDs
        sync_entities = self.db.query(SyncEntity).filter(
            SyncEntity.organization_id == self.organization_id,
            SyncEntity.source_system == "erpnext",
            SyncEntity.sync_status == SyncStatus.SYNCED,
        ).all()

        for se in sync_entities:
            if se.source_doctype == "Department":
                self._dept_cache[se.source_name] = se.target_id
            elif se.source_doctype == "Designation":
                self._desg_cache[se.source_name] = se.target_id
            elif se.source_doctype == "Employment Type":
                self._emptype_cache[se.source_name] = se.target_id
            elif se.source_doctype == "Employee Grade":
                self._grade_cache[se.source_name] = se.target_id
            elif se.source_doctype == "Employee":
                self._emp_cache[se.source_name] = se.target_id

    def _create_sync_entity(
        self,
        source_doctype: str,
        source_name: str,
        target_table: str,
        target_id: uuid.UUID,
    ) -> SyncEntity:
        """Create or update a sync entity record."""
        existing = self.db.query(SyncEntity).filter(
            SyncEntity.organization_id == self.organization_id,
            SyncEntity.source_system == "erpnext",
            SyncEntity.source_doctype == source_doctype,
            SyncEntity.source_name == source_name,
        ).first()

        if existing:
            existing.target_id = target_id
            existing.sync_status = SyncStatus.SYNCED
            existing.synced_at = datetime.utcnow()
            return existing

        sync_entity = SyncEntity(
            organization_id=self.organization_id,
            source_system="erpnext",
            source_doctype=source_doctype,
            source_name=source_name,
            target_table=target_table,
            target_id=target_id,
            sync_status=SyncStatus.SYNCED,
            synced_at=datetime.utcnow(),
        )
        self.db.add(sync_entity)
        return sync_entity

    def _import_departments(self, batch_id: uuid.UUID) -> ImportResult:
        """Import departments to production."""
        result = ImportResult()
        logger.info("Importing departments...")

        records = self.db.query(StagingDepartment).filter(
            StagingDepartment.batch_id == batch_id,
            StagingDepartment.organization_id == self.organization_id,
            StagingDepartment.validation_status == StagingStatus.VALID,
            StagingDepartment.imported_at.is_(None),
        ).all()

        result.total = len(records)

        # First pass: create all departments without parent links
        for staging in records:
            try:
                # Check if already exists
                if staging.source_name in self._dept_cache:
                    staging.validation_status = StagingStatus.SKIPPED
                    staging.imported_department_id = self._dept_cache[staging.source_name]
                    result.skipped += 1
                    continue

                dept = Department(
                    organization_id=self.organization_id,
                    department_code=staging.department_code[:20],
                    department_name=staging.department_name[:100],
                    is_active=staging.is_active,
                )
                self.db.add(dept)
                self.db.flush()

                # Update cache and staging
                self._dept_cache[staging.source_name] = dept.department_id
                staging.validation_status = StagingStatus.IMPORTED
                staging.imported_at = datetime.utcnow()
                staging.imported_department_id = dept.department_id

                # Create sync entity
                self._create_sync_entity(
                    source_doctype="Department",
                    source_name=staging.source_name,
                    target_table="hr.department",
                    target_id=dept.department_id,
                )

                result.imported += 1

            except Exception as e:
                logger.error(f"Error importing department {staging.source_name}: {e}")
                result.add_error(f"{staging.source_name}: {str(e)}")

        # Second pass: update parent links
        for staging in records:
            if staging.parent_department_name and staging.imported_department_id:
                parent_id = self._dept_cache.get(staging.parent_department_name)
                if parent_id:
                    dept_record = self.db.get(Department, staging.imported_department_id)
                    if dept_record:
                        dept_record.parent_department_id = parent_id

        self.db.flush()
        logger.info(f"Departments imported: {result.imported}, skipped: {result.skipped}")
        return result

    def _import_designations(self, batch_id: uuid.UUID) -> ImportResult:
        """Import designations to production."""
        result = ImportResult()
        logger.info("Importing designations...")

        records = self.db.query(StagingDesignation).filter(
            StagingDesignation.batch_id == batch_id,
            StagingDesignation.organization_id == self.organization_id,
            StagingDesignation.validation_status == StagingStatus.VALID,
            StagingDesignation.imported_at.is_(None),
        ).all()

        result.total = len(records)

        for staging in records:
            try:
                if staging.source_name in self._desg_cache:
                    staging.validation_status = StagingStatus.SKIPPED
                    staging.imported_designation_id = self._desg_cache[staging.source_name]
                    result.skipped += 1
                    continue

                desg = Designation(
                    organization_id=self.organization_id,
                    designation_code=staging.designation_code[:20],
                    designation_name=staging.designation_name[:100],
                    is_active=staging.is_active,
                )
                self.db.add(desg)
                self.db.flush()

                self._desg_cache[staging.source_name] = desg.designation_id
                staging.validation_status = StagingStatus.IMPORTED
                staging.imported_at = datetime.utcnow()
                staging.imported_designation_id = desg.designation_id

                self._create_sync_entity(
                    source_doctype="Designation",
                    source_name=staging.source_name,
                    target_table="hr.designation",
                    target_id=desg.designation_id,
                )

                result.imported += 1

            except Exception as e:
                logger.error(f"Error importing designation {staging.source_name}: {e}")
                result.add_error(f"{staging.source_name}: {str(e)}")

        self.db.flush()
        logger.info(f"Designations imported: {result.imported}, skipped: {result.skipped}")
        return result

    def _import_employment_types(self, batch_id: uuid.UUID) -> ImportResult:
        """Import employment types to production."""
        result = ImportResult()
        logger.info("Importing employment types...")

        records = self.db.query(StagingEmploymentType).filter(
            StagingEmploymentType.batch_id == batch_id,
            StagingEmploymentType.organization_id == self.organization_id,
            StagingEmploymentType.validation_status == StagingStatus.VALID,
            StagingEmploymentType.imported_at.is_(None),
        ).all()

        result.total = len(records)

        for staging in records:
            try:
                if staging.source_name in self._emptype_cache:
                    staging.validation_status = StagingStatus.SKIPPED
                    staging.imported_employment_type_id = self._emptype_cache[staging.source_name]
                    result.skipped += 1
                    continue

                emptype = EmploymentType(
                    organization_id=self.organization_id,
                    type_code=staging.type_code[:20],
                    type_name=staging.type_name[:100],
                    is_active=staging.is_active,
                )
                self.db.add(emptype)
                self.db.flush()

                self._emptype_cache[staging.source_name] = emptype.employment_type_id
                staging.validation_status = StagingStatus.IMPORTED
                staging.imported_at = datetime.utcnow()
                staging.imported_employment_type_id = emptype.employment_type_id

                self._create_sync_entity(
                    source_doctype="Employment Type",
                    source_name=staging.source_name,
                    target_table="hr.employment_type",
                    target_id=emptype.employment_type_id,
                )

                result.imported += 1

            except Exception as e:
                logger.error(f"Error importing employment type {staging.source_name}: {e}")
                result.add_error(f"{staging.source_name}: {str(e)}")

        self.db.flush()
        logger.info(f"Employment types imported: {result.imported}, skipped: {result.skipped}")
        return result

    def _import_employee_grades(self, batch_id: uuid.UUID) -> ImportResult:
        """Import employee grades to production."""
        result = ImportResult()
        logger.info("Importing employee grades...")

        records = self.db.query(StagingEmployeeGrade).filter(
            StagingEmployeeGrade.batch_id == batch_id,
            StagingEmployeeGrade.organization_id == self.organization_id,
            StagingEmployeeGrade.validation_status == StagingStatus.VALID,
            StagingEmployeeGrade.imported_at.is_(None),
        ).all()

        result.total = len(records)

        for staging in records:
            try:
                if staging.source_name in self._grade_cache:
                    staging.validation_status = StagingStatus.SKIPPED
                    staging.imported_grade_id = self._grade_cache[staging.source_name]
                    result.skipped += 1
                    continue

                grade = EmployeeGrade(
                    organization_id=self.organization_id,
                    grade_code=staging.grade_code[:20],
                    grade_name=staging.grade_name[:100],
                    min_salary=staging.default_base_pay,
                    is_active=staging.is_active,
                )
                self.db.add(grade)
                self.db.flush()

                self._grade_cache[staging.source_name] = grade.grade_id
                staging.validation_status = StagingStatus.IMPORTED
                staging.imported_at = datetime.utcnow()
                staging.imported_grade_id = grade.grade_id

                self._create_sync_entity(
                    source_doctype="Employee Grade",
                    source_name=staging.source_name,
                    target_table="hr.employee_grade",
                    target_id=grade.grade_id,
                )

                result.imported += 1

            except Exception as e:
                logger.error(f"Error importing employee grade {staging.source_name}: {e}")
                result.add_error(f"{staging.source_name}: {str(e)}")

        self.db.flush()
        logger.info(f"Employee grades imported: {result.imported}, skipped: {result.skipped}")
        return result

    def _import_employees(
        self,
        batch_id: uuid.UUID,
        generate_placeholder_emails: bool = True,
    ) -> ImportResult:
        """Import employees to production."""
        result = ImportResult()
        logger.info("Importing employees...")

        records = self.db.query(StagingEmployee).filter(
            StagingEmployee.batch_id == batch_id,
            StagingEmployee.organization_id == self.organization_id,
            StagingEmployee.validation_status == StagingStatus.VALID,
            StagingEmployee.imported_at.is_(None),
        ).all()

        result.total = len(records)

        for staging in records:
            try:
                # Check if already imported via SyncEntity
                if staging.source_name in self._emp_cache:
                    staging.validation_status = StagingStatus.SKIPPED
                    staging.imported_employee_id = self._emp_cache[staging.source_name]
                    result.skipped += 1
                    continue

                # Also check if employee already exists by employee_code
                # (handles orphaned records from previous failed syncs)
                existing_emp = self.db.query(Employee).filter(
                    Employee.organization_id == self.organization_id,
                    Employee.employee_code == staging.employee_code[:20],
                ).first()
                if existing_emp:
                    logger.info(f"Employee {staging.employee_code} already exists, skipping")
                    # Add to cache and create sync entity to track it
                    self._emp_cache[staging.source_name] = existing_emp.employee_id
                    staging.validation_status = StagingStatus.SKIPPED
                    staging.imported_employee_id = existing_emp.employee_id
                    staging.imported_person_id = existing_emp.person_id
                    self._create_sync_entity(
                        source_doctype="Employee",
                        source_name=staging.source_name,
                        target_table="hr.employee",
                        target_id=existing_emp.employee_id,
                    )
                    result.skipped += 1
                    continue

                # Determine email
                email = staging.preferred_email
                if not email and generate_placeholder_emails:
                    email = f"{staging.employee_code.lower()}@sync.internal"

                if not email:
                    result.add_error(f"{staging.source_name}: No email and placeholder generation disabled")
                    continue

                # Find or create Person (also checks for existing employee link)
                person_id, existing_employee_id = self._find_or_create_person(staging, email)

                # If Person is already linked to an Employee, skip
                if existing_employee_id:
                    logger.info(
                        f"Person for {staging.employee_code} already linked to employee, skipping"
                    )
                    self._emp_cache[staging.source_name] = existing_employee_id
                    staging.validation_status = StagingStatus.SKIPPED
                    staging.imported_employee_id = existing_employee_id
                    staging.imported_person_id = person_id
                    self._create_sync_entity(
                        source_doctype="Employee",
                        source_name=staging.source_name,
                        target_table="hr.employee",
                        target_id=existing_employee_id,
                    )
                    result.skipped += 1
                    continue

                # Resolve foreign keys
                department_id = (
                    self._dept_cache.get(staging.department_name)
                    if staging.department_name
                    else None
                )
                designation_id = (
                    self._desg_cache.get(staging.designation_name)
                    if staging.designation_name
                    else None
                )
                employment_type_id = (
                    self._emptype_cache.get(staging.employment_type_name)
                    if staging.employment_type_name
                    else None
                )
                grade_id = (
                    self._grade_cache.get(staging.grade_name)
                    if staging.grade_name
                    else None
                )
                reports_to_id = (
                    self._emp_cache.get(staging.reports_to_name)
                    if staging.reports_to_name
                    else None
                )

                # Map status
                status = EmployeeStatus.ACTIVE
                if staging.status:
                    try:
                        status = EmployeeStatus(staging.status.upper())
                    except ValueError:
                        if staging.status.lower() == "left":
                            status = EmployeeStatus.TERMINATED

                # Map gender
                gender = None
                if staging.gender:
                    try:
                        gender = Gender(staging.gender.upper())
                    except ValueError:
                        pass

                employee = Employee(
                    organization_id=self.organization_id,
                    person_id=person_id,
                    employee_code=staging.employee_code[:20],
                    gender=gender,
                    date_of_birth=staging.date_of_birth,
                    department_id=department_id,
                    designation_id=designation_id,
                    employment_type_id=employment_type_id,
                    grade_id=grade_id,
                    reports_to_id=reports_to_id,
                    date_of_joining=staging.date_of_joining,
                    date_of_leaving=staging.date_of_leaving,
                    status=status,
                    bank_name=staging.bank_name[:100] if staging.bank_name else None,
                    bank_account_number=staging.bank_ac_no[:50] if staging.bank_ac_no else None,
                )
                self.db.add(employee)
                self.db.flush()

                # Update caches and staging
                self._emp_cache[staging.source_name] = employee.employee_id
                staging.validation_status = StagingStatus.IMPORTED
                staging.imported_at = datetime.utcnow()
                staging.imported_employee_id = employee.employee_id
                staging.imported_person_id = person_id

                self._create_sync_entity(
                    source_doctype="Employee",
                    source_name=staging.source_name,
                    target_table="hr.employee",
                    target_id=employee.employee_id,
                )

                result.imported += 1

            except Exception as e:
                logger.error(f"Error importing employee {staging.source_name}: {e}")
                result.add_error(f"{staging.source_name}: {str(e)}")

        # Second pass: update reports_to references
        for staging in records:
            if staging.reports_to_name and staging.imported_employee_id:
                manager_id = self._emp_cache.get(staging.reports_to_name)
                if manager_id:
                    emp = self.db.get(Employee, staging.imported_employee_id)
                    if emp and not emp.reports_to_id:
                        emp.reports_to_id = manager_id

        self.db.flush()
        logger.info(f"Employees imported: {result.imported}, skipped: {result.skipped}")
        return result

    def _find_or_create_person(
        self, staging: StagingEmployee, email: str
    ) -> tuple[uuid.UUID, Optional[uuid.UUID]]:
        """Find or create a Person record for the employee.

        Returns:
            Tuple of (person_id, existing_employee_id).
            existing_employee_id is None if Person is not linked to any Employee.
        """
        # Try to find existing Person by email
        person = self.db.execute(
            select(Person).where(Person.email == email)
        ).scalar_one_or_none()

        if person:
            # Check if this Person is already linked to an Employee
            existing_employee = self.db.query(Employee).filter(
                Employee.person_id == person.id
            ).first()

            if existing_employee:
                return person.id, existing_employee.employee_id

            return person.id, None

        # Parse name
        first_name = staging.first_name or "Unknown"
        last_name = staging.last_name or ""

        if first_name == "Unknown" and staging.employee_name:
            name_parts = staging.employee_name.split()
            if name_parts:
                first_name = name_parts[0]
                last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Create new Person
        person = Person(
            organization_id=self.organization_id,
            first_name=first_name[:100],
            last_name=last_name[:100] if last_name else None,
            email=email,
            phone=staging.cell_number,
        )
        self.db.add(person)
        self.db.flush()

        return person.id, None
