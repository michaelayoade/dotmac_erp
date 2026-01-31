"""
Data Completeness Report Service.

Identifies employees with missing data required for statutory exports
(PAYE, Pension, NHF, Bank Upload). Helps payroll admins ensure compliance
before generating export files.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional
from uuid import UUID

from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.models.people.payroll.salary_assignment import (
    SalaryStructureAssignment,
)
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.services.finance.banking.bank_directory import BankDirectoryService

logger = logging.getLogger(__name__)


class ExportType(str, Enum):
    """Types of statutory exports."""

    PAYE = "paye"
    PENSION = "pension"
    NHF = "nhf"
    BANK_UPLOAD = "bank_upload"


class CompletenessStatus(str, Enum):
    """Data completeness status."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    MISSING_PROFILE = "missing_profile"


@dataclass
class MissingField:
    """A missing field for an employee."""

    field_name: str
    field_label: str
    export_types: list[ExportType]
    severity: str = "required"  # required, recommended


@dataclass
class EmployeeCompleteness:
    """Data completeness for a single employee."""

    employee_id: UUID
    employee_number: str
    full_name: str
    department_name: Optional[str]
    status: CompletenessStatus
    missing_fields: list[MissingField] = field(default_factory=list)
    has_tax_profile: bool = False
    has_bank_details: bool = False
    bank_code_resolvable: bool = False

    @property
    def is_paye_ready(self) -> bool:
        """Check if employee has all PAYE required fields."""
        return not any(
            ExportType.PAYE in f.export_types and f.severity == "required"
            for f in self.missing_fields
        )

    @property
    def is_pension_ready(self) -> bool:
        """Check if employee has all Pension required fields."""
        return not any(
            ExportType.PENSION in f.export_types and f.severity == "required"
            for f in self.missing_fields
        )

    @property
    def is_nhf_ready(self) -> bool:
        """Check if employee has all NHF required fields."""
        return not any(
            ExportType.NHF in f.export_types and f.severity == "required"
            for f in self.missing_fields
        )

    @property
    def is_bank_ready(self) -> bool:
        """Check if employee has all Bank Upload required fields."""
        return not any(
            ExportType.BANK_UPLOAD in f.export_types and f.severity == "required"
            for f in self.missing_fields
        )


@dataclass
class CompletenessReportResult:
    """Result of data completeness report generation."""

    organization_id: UUID
    report_date: date
    total_employees: int
    complete_count: int
    incomplete_count: int
    missing_profile_count: int
    employees: list[EmployeeCompleteness]

    # Aggregate counts by export type
    paye_ready_count: int = 0
    pension_ready_count: int = 0
    nhf_ready_count: int = 0
    bank_ready_count: int = 0

    @property
    def completeness_rate(self) -> float:
        """Overall completeness percentage."""
        if self.total_employees == 0:
            return 100.0
        return (self.complete_count / self.total_employees) * 100

    @property
    def incomplete_employees(self) -> list[EmployeeCompleteness]:
        """Get only employees with incomplete data."""
        return [e for e in self.employees if e.status != CompletenessStatus.COMPLETE]


class DataCompletenessService:
    """
    Service for checking employee data completeness for statutory exports.

    Validates that employees have all required fields for:
    - PAYE: TIN
    - Pension: RSA PIN, PFA Code
    - NHF: NHF Number
    - Bank Upload: Bank account number, Bank name (resolvable to code)
    """

    def __init__(self, db: Session):
        self.db = db
        self.bank_directory = BankDirectoryService(db)

    def generate_report(
        self,
        organization_id: UUID,
        *,
        department_id: Optional[UUID] = None,
        designation_id: Optional[UUID] = None,
        include_complete: bool = True,
        export_types: Optional[list[ExportType]] = None,
    ) -> CompletenessReportResult:
        """
        Generate data completeness report for employees.

        Args:
            organization_id: Organization ID
            department_id: Optional department filter
            designation_id: Optional designation filter
            include_complete: Whether to include employees with complete data
            export_types: Specific export types to check (default: all)

        Returns:
            CompletenessReportResult with employee-level details
        """
        today = date.today()
        check_types = export_types or list(ExportType)

        # Get employees with active salary structure assignments
        employees = self._get_assigned_employees(
            organization_id, department_id, designation_id
        )

        # Get current tax profiles for all employees
        tax_profiles = self._get_current_tax_profiles(
            organization_id, [e.employee_id for e in employees], today
        )

        # Build lookup dict
        profile_by_employee: dict[UUID, EmployeeTaxProfile] = {
            p.employee_id: p for p in tax_profiles
        }

        results: list[EmployeeCompleteness] = []
        complete_count = 0
        incomplete_count = 0
        missing_profile_count = 0
        paye_ready = 0
        pension_ready = 0
        nhf_ready = 0
        bank_ready = 0

        for employee in employees:
            completeness = self._check_employee_completeness(
                employee, profile_by_employee.get(employee.employee_id), check_types
            )
            results.append(completeness)

            # Count by status
            if completeness.status == CompletenessStatus.COMPLETE:
                complete_count += 1
            elif completeness.status == CompletenessStatus.MISSING_PROFILE:
                missing_profile_count += 1
                incomplete_count += 1
            else:
                incomplete_count += 1

            # Count by export readiness
            if completeness.is_paye_ready:
                paye_ready += 1
            if completeness.is_pension_ready:
                pension_ready += 1
            if completeness.is_nhf_ready:
                nhf_ready += 1
            if completeness.is_bank_ready:
                bank_ready += 1

        # Filter out complete if not requested
        if not include_complete:
            results = [
                e for e in results if e.status != CompletenessStatus.COMPLETE
            ]

        return CompletenessReportResult(
            organization_id=organization_id,
            report_date=today,
            total_employees=len(employees),
            complete_count=complete_count,
            incomplete_count=incomplete_count,
            missing_profile_count=missing_profile_count,
            employees=results,
            paye_ready_count=paye_ready,
            pension_ready_count=pension_ready,
            nhf_ready_count=nhf_ready,
            bank_ready_count=bank_ready,
        )

    def _get_assigned_employees(
        self,
        organization_id: UUID,
        department_id: Optional[UUID],
        designation_id: Optional[UUID],
    ) -> list[Employee]:
        """Get employees with active salary structure assignments."""
        today = date.today()

        # Subquery for employees with active assignments
        assignment_subq = (
            select(SalaryStructureAssignment.employee_id)
            .where(
                SalaryStructureAssignment.organization_id == organization_id,
                SalaryStructureAssignment.from_date <= today,
                or_(
                    SalaryStructureAssignment.to_date.is_(None),
                    SalaryStructureAssignment.to_date >= today,
                ),
            )
            .distinct()
        )

        stmt = (
            select(Employee)
            .options(joinedload(Employee.department), joinedload(Employee.designation))
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.employee_id.in_(assignment_subq),
            )
            .join(Person, Employee.person_id == Person.id)
            .order_by(Person.display_name, Person.last_name, Person.first_name)
        )

        if department_id:
            stmt = stmt.where(Employee.department_id == department_id)
        if designation_id:
            stmt = stmt.where(Employee.designation_id == designation_id)

        return list(self.db.scalars(stmt).unique().all())

    def _get_current_tax_profiles(
        self,
        organization_id: UUID,
        employee_ids: list[UUID],
        as_of: date,
    ) -> list[EmployeeTaxProfile]:
        """Get current tax profiles for employees."""
        if not employee_ids:
            return []

        stmt = select(EmployeeTaxProfile).where(
            EmployeeTaxProfile.organization_id == organization_id,
            EmployeeTaxProfile.employee_id.in_(employee_ids),
            EmployeeTaxProfile.effective_from <= as_of,
            or_(
                EmployeeTaxProfile.effective_to.is_(None),
                EmployeeTaxProfile.effective_to >= as_of,
            ),
        )

        return list(self.db.scalars(stmt).all())

    def _check_employee_completeness(
        self,
        employee: Employee,
        tax_profile: Optional[EmployeeTaxProfile],
        export_types: list[ExportType],
    ) -> EmployeeCompleteness:
        """Check data completeness for a single employee."""
        missing_fields: list[MissingField] = []

        # Check if tax profile exists
        has_tax_profile = tax_profile is not None

        # Check bank details
        has_bank_details = bool(
            employee.bank_account_number and employee.bank_name
        )
        bank_code_resolvable = False
        if employee.bank_name:
            code = self.bank_directory.lookup_bank_code(employee.bank_name)
            bank_code_resolvable = code is not None

        # PAYE requirements
        if ExportType.PAYE in export_types:
            if not tax_profile or not tax_profile.tin:
                missing_fields.append(
                    MissingField(
                        field_name="tin",
                        field_label="Tax Identification Number (TIN)",
                        export_types=[ExportType.PAYE],
                        severity="required",
                    )
                )

        # Pension requirements
        if ExportType.PENSION in export_types:
            if not tax_profile or not tax_profile.rsa_pin:
                missing_fields.append(
                    MissingField(
                        field_name="rsa_pin",
                        field_label="RSA PIN",
                        export_types=[ExportType.PENSION],
                        severity="required",
                    )
                )
            if not tax_profile or not tax_profile.pfa_code:
                missing_fields.append(
                    MissingField(
                        field_name="pfa_code",
                        field_label="PFA Code",
                        export_types=[ExportType.PENSION],
                        severity="required",
                    )
                )

        # NHF requirements
        if ExportType.NHF in export_types:
            if not tax_profile or not tax_profile.nhf_number:
                missing_fields.append(
                    MissingField(
                        field_name="nhf_number",
                        field_label="NHF Number",
                        export_types=[ExportType.NHF],
                        severity="required",
                    )
                )

        # Bank Upload requirements
        if ExportType.BANK_UPLOAD in export_types:
            if not employee.bank_account_number:
                missing_fields.append(
                    MissingField(
                        field_name="bank_account_number",
                        field_label="Bank Account Number",
                        export_types=[ExportType.BANK_UPLOAD],
                        severity="required",
                    )
                )
            if not employee.bank_name:
                missing_fields.append(
                    MissingField(
                        field_name="bank_name",
                        field_label="Bank Name",
                        export_types=[ExportType.BANK_UPLOAD],
                        severity="required",
                    )
                )
            elif not bank_code_resolvable:
                missing_fields.append(
                    MissingField(
                        field_name="bank_code",
                        field_label="Bank Code (unrecognized bank name)",
                        export_types=[ExportType.BANK_UPLOAD],
                        severity="required",
                    )
                )

        # Determine status
        if not has_tax_profile and any(
            et in export_types
            for et in [ExportType.PAYE, ExportType.PENSION, ExportType.NHF]
        ):
            status = CompletenessStatus.MISSING_PROFILE
        elif missing_fields:
            status = CompletenessStatus.INCOMPLETE
        else:
            status = CompletenessStatus.COMPLETE

        dept_name = None
        if employee.department:
            dept_name = employee.department.department_name

        return EmployeeCompleteness(
            employee_id=employee.employee_id,
            employee_number=employee.employee_code,
            full_name=employee.full_name,
            department_name=dept_name,
            status=status,
            missing_fields=missing_fields,
            has_tax_profile=has_tax_profile,
            has_bank_details=has_bank_details,
            bank_code_resolvable=bank_code_resolvable,
        )

    def get_summary_stats(
        self, organization_id: UUID
    ) -> dict:
        """
        Get quick summary statistics for data completeness.

        Returns dict with counts without full employee details.
        """
        today = date.today()

        # Count assigned employees
        assignment_subq = (
            select(SalaryStructureAssignment.employee_id)
            .where(
                SalaryStructureAssignment.organization_id == organization_id,
                SalaryStructureAssignment.from_date <= today,
                or_(
                    SalaryStructureAssignment.to_date.is_(None),
                    SalaryStructureAssignment.to_date >= today,
                ),
            )
            .distinct()
        )

        total_assigned = self.db.scalar(
            select(func.count())
            .select_from(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.employee_id.in_(assignment_subq),
            )
        ) or 0

        # Count with tax profiles
        profile_subq = (
            select(EmployeeTaxProfile.employee_id)
            .where(
                EmployeeTaxProfile.organization_id == organization_id,
                EmployeeTaxProfile.effective_from <= today,
                or_(
                    EmployeeTaxProfile.effective_to.is_(None),
                    EmployeeTaxProfile.effective_to >= today,
                ),
            )
            .distinct()
        )

        with_profile = self.db.scalar(
            select(func.count())
            .select_from(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.employee_id.in_(assignment_subq),
                Employee.employee_id.in_(profile_subq),
            )
        ) or 0

        # Count with bank details
        with_bank = self.db.scalar(
            select(func.count())
            .select_from(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.employee_id.in_(assignment_subq),
                Employee.bank_account_number.isnot(None),
                Employee.bank_name.isnot(None),
            )
        ) or 0

        return {
            "total_assigned_employees": total_assigned,
            "with_tax_profile": with_profile,
            "without_tax_profile": total_assigned - with_profile,
            "with_bank_details": with_bank,
            "without_bank_details": total_assigned - with_bank,
        }


def data_completeness_service(db: Session) -> DataCompletenessService:
    """Create a DataCompletenessService instance."""
    return DataCompletenessService(db)
