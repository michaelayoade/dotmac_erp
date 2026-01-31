"""
Pension Export Service.

Generates pension contribution schedules for PFAs.
Supports Paypen and generic formats for uploading to PenCom CPRS.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.payroll.salary_slip import (
    SalarySlip,
    SalarySlipStatus,
    SalarySlipEarning,
    SalarySlipDeduction,
)

logger = logging.getLogger(__name__)

PensionFormat = Literal["paypen", "generic"]


def _round_currency(value: Decimal) -> Decimal:
    """Round to 2 decimal places using ROUND_HALF_UP."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class PensionExportResult:
    """Result of pension export generation."""

    content: bytes
    filename: str
    content_type: str
    employee_count: int
    total_employee_contribution: Decimal
    total_employer_contribution: Decimal
    total_contribution: Decimal
    errors: list[str]


class PensionExportService:
    """
    Service for generating pension contribution schedules.

    Generates files for uploading to:
    - Paypen (common format for many PFAs)
    - PenCom CPRS (Contribution Payment Report Schedule)
    """

    def __init__(self, db: Session):
        self.db = db

    def generate_export(
        self,
        organization_id: UUID,
        year: int,
        month: int,
        pension_format: PensionFormat = "paypen",
        entry_id: Optional[UUID] = None,
    ) -> PensionExportResult:
        """
        Generate pension export for a period.

        Args:
            organization_id: Organization ID
            year: Contribution year
            month: Contribution month
            pension_format: Target format
            entry_id: Optional payroll entry ID to filter by

        Returns:
            PensionExportResult with file content and metadata
        """
        slips = self._get_slips(organization_id, year, month, entry_id)

        if pension_format == "paypen":
            return self._generate_paypen_format(slips, year, month)
        else:
            return self._generate_generic_format(slips, year, month)

    def _get_slips(
        self,
        organization_id: UUID,
        year: int,
        month: int,
        entry_id: Optional[UUID] = None,
    ) -> list[SalarySlip]:
        """Get salary slips for the period."""
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month + 1, 1)

        stmt = (
            select(SalarySlip)
            .options(
                joinedload(SalarySlip.employee),
                joinedload(SalarySlip.earnings).joinedload(SalarySlipEarning.component),
                joinedload(SalarySlip.deductions).joinedload(SalarySlipDeduction.component),
            )
            .where(
                SalarySlip.organization_id == organization_id,
                SalarySlip.status.in_([SalarySlipStatus.POSTED, SalarySlipStatus.PAID]),
                SalarySlip.start_date >= start_date,
                SalarySlip.start_date < end_date,
            )
        )

        if entry_id:
            stmt = stmt.where(SalarySlip.payroll_entry_id == entry_id)

        return list(self.db.scalars(stmt).all())

    def _generate_paypen_format(
        self,
        slips: list[SalarySlip],
        year: int,
        month: int,
    ) -> PensionExportResult:
        """
        Generate Paypen format for pension contributions.

        Standard format used by many PFAs:
        - PFA Code
        - Staff ID
        - RSA PIN
        - Employee Name
        - Employee Contribution
        - Employer Contribution
        - Total Contribution
        - Period (MMYYYY)
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            "PFA_CODE",
            "STAFF_ID",
            "RSA_PIN",
            "EMPLOYEE_NAME",
            "EMPLOYEE_CONTRIBUTION",
            "EMPLOYER_CONTRIBUTION",
            "TOTAL_CONTRIBUTION",
            "PERIOD",
        ])

        errors: list[str] = []
        total_employee = Decimal("0")
        total_employer = Decimal("0")
        employee_count = 0
        period = f"{month:02d}{year}"

        for slip in slips:
            employee = slip.employee
            if not employee:
                errors.append(f"Slip {slip.slip_number}: No employee data")
                continue

            # Get pension amounts from deductions
            deduction_map = self._extract_deduction_map(slip)
            pension_employee = deduction_map.get("PENSION", Decimal("0"))
            pension_employer = deduction_map.get("PENSION_EMPLOYER", Decimal("0"))

            # Skip if no pension contribution
            if pension_employee == 0 and pension_employer == 0:
                continue

            # Get tax profile for PFA and RSA PIN
            tax_profile = getattr(employee, "current_tax_profile", None)
            pfa_code = ""
            rsa_pin = ""

            if tax_profile:
                pfa_code = tax_profile.pfa_code or ""
                rsa_pin = tax_profile.rsa_pin or ""

            # Track missing data
            if not rsa_pin:
                errors.append(f"{employee.full_name}: Missing RSA PIN")
            if not pfa_code:
                errors.append(f"{employee.full_name}: Missing PFA Code")

            staff_id = employee.employee_code
            total_contribution = pension_employee + pension_employer

            writer.writerow([
                pfa_code,
                staff_id,
                rsa_pin,
                employee.full_name,
                str(_round_currency(pension_employee)),
                str(_round_currency(pension_employer)),
                str(_round_currency(total_contribution)),
                period,
            ])

            total_employee += pension_employee
            total_employer += pension_employer
            employee_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"Pension_Paypen_{year}_{month:02d}.csv"

        return PensionExportResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            employee_count=employee_count,
            total_employee_contribution=_round_currency(total_employee),
            total_employer_contribution=_round_currency(total_employer),
            total_contribution=_round_currency(total_employee + total_employer),
            errors=errors,
        )

    def _generate_generic_format(
        self,
        slips: list[SalarySlip],
        year: int,
        month: int,
    ) -> PensionExportResult:
        """
        Generate generic pension contribution format.

        Includes more detail for internal reporting.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            "S/N",
            "Staff ID",
            "Employee Name",
            "PFA Code",
            "PFA Name",
            "RSA PIN",
            "Basic Salary",
            "Housing",
            "Transport",
            "BHT (Pension Base)",
            "Employee Contribution (8%)",
            "Employer Contribution (10%)",
            "Total Contribution",
            "Period",
        ])

        errors: list[str] = []
        total_employee = Decimal("0")
        total_employer = Decimal("0")
        employee_count = 0
        period_str = f"{year}-{month:02d}"

        for idx, slip in enumerate(slips, start=1):
            employee = slip.employee
            if not employee:
                errors.append(f"Slip {slip.slip_number}: No employee data")
                continue

            # Get pension amounts
            deduction_map = self._extract_deduction_map(slip)
            pension_employee = deduction_map.get("PENSION", Decimal("0"))
            pension_employer = deduction_map.get("PENSION_EMPLOYER", Decimal("0"))

            if pension_employee == 0 and pension_employer == 0:
                continue

            # Get tax profile
            tax_profile = getattr(employee, "current_tax_profile", None)
            pfa_code = ""
            pfa_name = ""
            rsa_pin = ""

            if tax_profile:
                pfa_code = tax_profile.pfa_code or ""
                rsa_pin = tax_profile.rsa_pin or ""
                if tax_profile.pfa:
                    pfa_name = tax_profile.pfa.pfa_name

            # Get earnings breakdown
            basic = Decimal("0")
            housing = Decimal("0")
            transport = Decimal("0")

            for earning in slip.earnings:
                component_code = (earning.component.component_code if earning.component else "").upper()
                amount = earning.amount or Decimal("0")

                if component_code == "BASIC":
                    basic += amount
                elif component_code == "HOUSING":
                    housing += amount
                elif component_code == "TRANSPORT":
                    transport += amount

            bht = basic + housing + transport
            total_contribution = pension_employee + pension_employer

            writer.writerow([
                idx,
                    employee.employee_code,
                employee.full_name,
                pfa_code,
                pfa_name,
                rsa_pin,
                str(_round_currency(basic)),
                str(_round_currency(housing)),
                str(_round_currency(transport)),
                str(_round_currency(bht)),
                str(_round_currency(pension_employee)),
                str(_round_currency(pension_employer)),
                str(_round_currency(total_contribution)),
                period_str,
            ])

            total_employee += pension_employee
            total_employer += pension_employer
            employee_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"Pension_Schedule_{year}_{month:02d}.csv"

        return PensionExportResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            employee_count=employee_count,
            total_employee_contribution=_round_currency(total_employee),
            total_employer_contribution=_round_currency(total_employer),
            total_contribution=_round_currency(total_employee + total_employer),
            errors=errors,
        )

    def _extract_deduction_map(self, slip: SalarySlip) -> dict[str, Decimal]:
        """Map deduction component codes to totals."""
        totals: dict[str, Decimal] = {}
        for deduction in slip.deductions:
            component_code = (deduction.component.component_code if deduction.component else "").upper()
            if not component_code:
                continue
            totals[component_code] = totals.get(component_code, Decimal("0")) + (deduction.amount or Decimal("0"))
        return totals


def pension_export_service(db: Session) -> PensionExportService:
    """Create a PensionExportService instance."""
    return PensionExportService(db)
