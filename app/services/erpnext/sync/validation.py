"""
Staging Data Validation Service.

Validates data in staging tables and generates reports on data quality issues.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

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


@dataclass
class ValidationIssue:
    """Represents a single validation issue."""

    entity_type: str
    source_name: str
    field: str
    issue_type: str  # ERROR, WARNING
    message: str
    suggested_fix: str | None = None


@dataclass
class ValidationReport:
    """Complete validation report for a batch."""

    batch_id: uuid.UUID
    validated_at: datetime
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    warning_records: int = 0

    # Issues grouped by type
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    # Summary by entity type
    summary: dict = field(default_factory=dict)

    def add_error(self, issue: ValidationIssue):
        self.errors.append(issue)

    def add_warning(self, issue: ValidationIssue):
        self.warnings.append(issue)


class StagingValidationService:
    """
    Validates staging data and generates quality reports.

    Checks for:
    - Missing required fields
    - Duplicate values (emails, codes)
    - Invalid references (department, designation not found)
    - Data format issues
    """

    def __init__(self, db: Session, organization_id: uuid.UUID):
        self.db = db
        self.organization_id = organization_id

    def validate_batch(self, batch_id: uuid.UUID) -> ValidationReport:
        """
        Validate all staging records in a batch.

        Args:
            batch_id: The batch to validate

        Returns:
            ValidationReport with all issues found
        """
        report = ValidationReport(
            batch_id=batch_id,
            validated_at=datetime.utcnow(),
        )

        logger.info(f"Validating batch {batch_id}")

        # Validate each entity type
        self._validate_departments(batch_id, report)
        self._validate_designations(batch_id, report)
        self._validate_employment_types(batch_id, report)
        self._validate_employee_grades(batch_id, report)
        self._validate_employees(batch_id, report)

        # Update batch status
        batch = self.db.get(StagingSyncBatch, batch_id)
        if batch:
            batch.status = "VALIDATED"
            batch.validated_at = datetime.utcnow()
            batch.valid_records = report.valid_records
            batch.invalid_records = report.invalid_records
            batch.validation_summary = {
                "total_errors": len(report.errors),
                "total_warnings": len(report.warnings),
                "summary": report.summary,
            }
            self.db.commit()

        logger.info(
            f"Validation complete: {report.valid_records} valid, "
            f"{report.invalid_records} invalid, {len(report.warnings)} warnings"
        )

        return report

    def _validate_departments(self, batch_id: uuid.UUID, report: ValidationReport):
        """Validate department records."""
        records = (
            self.db.query(StagingDepartment)
            .filter(
                StagingDepartment.batch_id == batch_id,
                StagingDepartment.organization_id == self.organization_id,
            )
            .all()
        )

        valid = 0
        invalid = 0
        dept_names = set()

        for dept in records:
            errors = []
            warnings = []

            # Check required fields
            if not dept.department_code:
                errors.append("Missing department_code")
            if not dept.department_name:
                errors.append("Missing department_name")

            # Check for duplicates within batch
            if dept.department_name in dept_names:
                warnings.append(f"Duplicate department name: {dept.department_name}")
            dept_names.add(dept.department_name)

            # Check parent reference exists
            if dept.parent_department_name:
                parent_exists = (
                    self.db.query(StagingDepartment)
                    .filter(
                        StagingDepartment.batch_id == batch_id,
                        StagingDepartment.source_name == dept.parent_department_name,
                    )
                    .first()
                )
                if not parent_exists:
                    warnings.append(
                        f"Parent department not found: {dept.parent_department_name}"
                    )

            # Update record status
            if errors:
                dept.validation_status = StagingStatus.INVALID
                dept.validation_errors = errors
                invalid += 1
                for err in errors:
                    report.add_error(
                        ValidationIssue(
                            entity_type="department",
                            source_name=dept.source_name,
                            field="",
                            issue_type="ERROR",
                            message=err,
                        )
                    )
            else:
                dept.validation_status = StagingStatus.VALID
                valid += 1

            if warnings:
                dept.validation_warnings = warnings
                for warn in warnings:
                    report.add_warning(
                        ValidationIssue(
                            entity_type="department",
                            source_name=dept.source_name,
                            field="",
                            issue_type="WARNING",
                            message=warn,
                        )
                    )

        report.total_records += len(records)
        report.valid_records += valid
        report.invalid_records += invalid
        report.summary["departments"] = {
            "total": len(records),
            "valid": valid,
            "invalid": invalid,
        }
        self.db.flush()

    def _validate_designations(self, batch_id: uuid.UUID, report: ValidationReport):
        """Validate designation records."""
        records = (
            self.db.query(StagingDesignation)
            .filter(
                StagingDesignation.batch_id == batch_id,
                StagingDesignation.organization_id == self.organization_id,
            )
            .all()
        )

        valid = 0
        invalid = 0

        for desg in records:
            errors = []

            if not desg.designation_code:
                errors.append("Missing designation_code")
            if not desg.designation_name:
                errors.append("Missing designation_name")

            if errors:
                desg.validation_status = StagingStatus.INVALID
                desg.validation_errors = errors
                invalid += 1
                for err in errors:
                    report.add_error(
                        ValidationIssue(
                            entity_type="designation",
                            source_name=desg.source_name,
                            field="",
                            issue_type="ERROR",
                            message=err,
                        )
                    )
            else:
                desg.validation_status = StagingStatus.VALID
                valid += 1

        report.total_records += len(records)
        report.valid_records += valid
        report.invalid_records += invalid
        report.summary["designations"] = {
            "total": len(records),
            "valid": valid,
            "invalid": invalid,
        }
        self.db.flush()

    def _validate_employment_types(self, batch_id: uuid.UUID, report: ValidationReport):
        """Validate employment type records."""
        records = (
            self.db.query(StagingEmploymentType)
            .filter(
                StagingEmploymentType.batch_id == batch_id,
                StagingEmploymentType.organization_id == self.organization_id,
            )
            .all()
        )

        valid = 0
        invalid = 0

        for etype in records:
            errors = []

            if not etype.type_code:
                errors.append("Missing type_code")
            if not etype.type_name:
                errors.append("Missing type_name")

            if errors:
                etype.validation_status = StagingStatus.INVALID
                etype.validation_errors = errors
                invalid += 1
            else:
                etype.validation_status = StagingStatus.VALID
                valid += 1

        report.total_records += len(records)
        report.valid_records += valid
        report.invalid_records += invalid
        report.summary["employment_types"] = {
            "total": len(records),
            "valid": valid,
            "invalid": invalid,
        }
        self.db.flush()

    def _validate_employee_grades(self, batch_id: uuid.UUID, report: ValidationReport):
        """Validate employee grade records."""
        records = (
            self.db.query(StagingEmployeeGrade)
            .filter(
                StagingEmployeeGrade.batch_id == batch_id,
                StagingEmployeeGrade.organization_id == self.organization_id,
            )
            .all()
        )

        valid = 0
        invalid = 0

        for grade in records:
            errors = []

            if not grade.grade_code:
                errors.append("Missing grade_code")
            if not grade.grade_name:
                errors.append("Missing grade_name")

            if errors:
                grade.validation_status = StagingStatus.INVALID
                grade.validation_errors = errors
                invalid += 1
            else:
                grade.validation_status = StagingStatus.VALID
                valid += 1

        report.total_records += len(records)
        report.valid_records += valid
        report.invalid_records += invalid
        report.summary["employee_grades"] = {
            "total": len(records),
            "valid": valid,
            "invalid": invalid,
        }
        self.db.flush()

    def _validate_employees(self, batch_id: uuid.UUID, report: ValidationReport):
        """Validate employee records - the main validation target."""
        records = (
            self.db.query(StagingEmployee)
            .filter(
                StagingEmployee.batch_id == batch_id,
                StagingEmployee.organization_id == self.organization_id,
            )
            .all()
        )

        valid = 0
        invalid = 0

        # Build lookup sets for reference validation
        dept_names = set(
            r.source_name
            for r in self.db.query(StagingDepartment.source_name)
            .filter(StagingDepartment.batch_id == batch_id)
            .all()
        )
        desg_names = set(
            r.source_name
            for r in self.db.query(StagingDesignation.source_name)
            .filter(StagingDesignation.batch_id == batch_id)
            .all()
        )
        emptype_names = set(
            r.source_name
            for r in self.db.query(StagingEmploymentType.source_name)
            .filter(StagingEmploymentType.batch_id == batch_id)
            .all()
        )
        grade_names = set(
            r.source_name
            for r in self.db.query(StagingEmployeeGrade.source_name)
            .filter(StagingEmployeeGrade.batch_id == batch_id)
            .all()
        )
        emp_names = set(r.source_name for r in records)

        # Check for duplicate emails
        email_counts: dict[str, list[str]] = {}
        for emp in records:
            email = emp.preferred_email
            if email:
                if email not in email_counts:
                    email_counts[email] = []
                email_counts[email].append(emp.source_name)

        duplicate_emails = {
            email: names for email, names in email_counts.items() if len(names) > 1
        }

        for emp in records:
            errors = []
            warnings = []

            # Required fields
            if not emp.employee_code:
                errors.append("Missing employee_code")
            if not emp.employee_name:
                errors.append("Missing employee_name")

            # Email validation
            if not emp.preferred_email:
                errors.append(
                    "Missing email (company_email or personal_email required)"
                )
            elif emp.preferred_email in duplicate_emails:
                other_emps = [
                    n
                    for n in duplicate_emails[emp.preferred_email]
                    if n != emp.source_name
                ]
                errors.append(
                    f"Duplicate email '{emp.preferred_email}' shared with: {', '.join(other_emps)}"
                )

            # Reference validation
            if emp.department_name and emp.department_name not in dept_names:
                warnings.append(
                    f"Department not found in staging: {emp.department_name}"
                )

            if emp.designation_name and emp.designation_name not in desg_names:
                warnings.append(
                    f"Designation not found in staging: {emp.designation_name}"
                )

            if (
                emp.employment_type_name
                and emp.employment_type_name not in emptype_names
            ):
                warnings.append(
                    f"Employment type not found in staging: {emp.employment_type_name}"
                )

            if emp.grade_name and emp.grade_name not in grade_names:
                warnings.append(f"Grade not found in staging: {emp.grade_name}")

            if emp.reports_to_name and emp.reports_to_name not in emp_names:
                warnings.append(
                    f"Reports-to employee not found in staging: {emp.reports_to_name}"
                )

            # Date validation
            if emp.date_of_leaving and emp.date_of_joining:
                if emp.date_of_leaving < emp.date_of_joining:
                    errors.append("date_of_leaving is before date_of_joining")

            # Update record status
            if errors:
                emp.validation_status = StagingStatus.INVALID
                emp.validation_errors = errors
                invalid += 1
                for err in errors:
                    report.add_error(
                        ValidationIssue(
                            entity_type="employee",
                            source_name=emp.source_name,
                            field="",
                            issue_type="ERROR",
                            message=err,
                        )
                    )
            else:
                emp.validation_status = StagingStatus.VALID
                valid += 1

            if warnings:
                emp.validation_warnings = warnings
                for warn in warnings:
                    report.add_warning(
                        ValidationIssue(
                            entity_type="employee",
                            source_name=emp.source_name,
                            field="",
                            issue_type="WARNING",
                            message=warn,
                        )
                    )

        report.total_records += len(records)
        report.valid_records += valid
        report.invalid_records += invalid
        report.summary["employees"] = {
            "total": len(records),
            "valid": valid,
            "invalid": invalid,
            "duplicate_emails": len(duplicate_emails),
        }
        self.db.flush()

    def get_duplicate_emails_report(self, batch_id: uuid.UUID) -> list[dict]:
        """
        Get detailed report of duplicate emails.

        Returns list of dicts with:
        - email: The duplicate email
        - employees: List of employee records sharing this email
        """
        records = (
            self.db.query(StagingEmployee)
            .filter(
                StagingEmployee.batch_id == batch_id,
                StagingEmployee.organization_id == self.organization_id,
            )
            .all()
        )

        email_groups: dict[str, list[dict]] = {}
        for emp in records:
            email = emp.preferred_email
            if email:
                if email not in email_groups:
                    email_groups[email] = []
                email_groups[email].append(
                    {
                        "source_name": emp.source_name,
                        "employee_code": emp.employee_code,
                        "employee_name": emp.employee_name,
                        "company_email": emp.company_email,
                        "personal_email": emp.personal_email,
                        "department": emp.department_name,
                        "status": emp.status,
                    }
                )

        # Filter to only duplicates
        duplicates = []
        for email, employees in email_groups.items():
            if len(employees) > 1:
                duplicates.append(
                    {
                        "email": email,
                        "count": len(employees),
                        "employees": employees,
                    }
                )

        return sorted(duplicates, key=lambda x: -x["count"])

    def get_missing_references_report(self, batch_id: uuid.UUID) -> dict:
        """
        Get report of missing foreign key references.

        Returns dict with missing references by type.
        """
        # Get all referenced values from employees
        employees = (
            self.db.query(StagingEmployee)
            .filter(
                StagingEmployee.batch_id == batch_id,
                StagingEmployee.organization_id == self.organization_id,
            )
            .all()
        )

        referenced_depts = set(
            e.department_name for e in employees if e.department_name
        )
        referenced_desgs = set(
            e.designation_name for e in employees if e.designation_name
        )
        referenced_types = set(
            e.employment_type_name for e in employees if e.employment_type_name
        )
        referenced_grades = set(e.grade_name for e in employees if e.grade_name)
        referenced_managers = set(
            e.reports_to_name for e in employees if e.reports_to_name
        )

        # Get existing staging records
        staged_depts = set(
            r.source_name
            for r in self.db.query(StagingDepartment.source_name)
            .filter(StagingDepartment.batch_id == batch_id)
            .all()
        )
        staged_desgs = set(
            r.source_name
            for r in self.db.query(StagingDesignation.source_name)
            .filter(StagingDesignation.batch_id == batch_id)
            .all()
        )
        staged_types = set(
            r.source_name
            for r in self.db.query(StagingEmploymentType.source_name)
            .filter(StagingEmploymentType.batch_id == batch_id)
            .all()
        )
        staged_grades = set(
            r.source_name
            for r in self.db.query(StagingEmployeeGrade.source_name)
            .filter(StagingEmployeeGrade.batch_id == batch_id)
            .all()
        )
        staged_emps = set(e.source_name for e in employees)

        return {
            "missing_departments": list(referenced_depts - staged_depts),
            "missing_designations": list(referenced_desgs - staged_desgs),
            "missing_employment_types": list(referenced_types - staged_types),
            "missing_grades": list(referenced_grades - staged_grades),
            "missing_managers": list(referenced_managers - staged_emps),
        }
