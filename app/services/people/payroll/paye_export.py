"""
PAYE Export Service.

Generates PAYE tax schedules for submission to state tax authorities.
Supports LIRS (Lagos) and FCTIRS (FCT/Abuja) formats.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.payroll.salary_slip import (
    SalarySlip,
    SalarySlipDeduction,
    SalarySlipEarning,
    SalarySlipStatus,
)

logger = logging.getLogger(__name__)

PAYEFormat = Literal["lirs", "fctirs"]


def _round_currency(value: Decimal) -> Decimal:
    """Round to 2 decimal places using ROUND_HALF_UP."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class PAYEExportResult:
    """Result of PAYE export generation."""

    content: bytes
    filename: str
    content_type: str
    employee_count: int
    total_tax: Decimal
    errors: list[str]


class PAYEExportService:
    """
    Service for generating PAYE tax schedules.

    Supports:
    - LIRS (Lagos Internal Revenue Service) format
    - FCTIRS (FCT Internal Revenue Service) format
    """

    def __init__(self, db: Session):
        self.db = db

    def generate_export(
        self,
        organization_id: UUID,
        year: int,
        month: int,
        paye_format: PAYEFormat = "lirs",
        entry_id: Optional[UUID] = None,
    ) -> PAYEExportResult:
        """
        Generate PAYE export for a period.

        Args:
            organization_id: Organization ID
            year: Tax year
            month: Tax month
            paye_format: Target format (lirs or fctirs)
            entry_id: Optional payroll entry ID to filter by

        Returns:
            PAYEExportResult with file content and metadata
        """
        slips = self._get_slips(organization_id, year, month, entry_id)

        if paye_format == "lirs":
            return self._generate_lirs_format(slips, year, month)
        else:
            return self._generate_fctirs_format(slips, year, month)

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
                joinedload(SalarySlip.deductions).joinedload(
                    SalarySlipDeduction.component
                ),
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

    def _generate_lirs_format(
        self,
        slips: list[SalarySlip],
        year: int,
        month: int,
    ) -> PAYEExportResult:
        """
        Generate LIRS (Lagos) PAYE format.

        Columns based on LIRS template:
        name, tax_payer_number, nationality, designation, no_of_months,
        1-Basic Salary, 2-Housing, 3-Transport, 4-Furniture, 5-Education,
        6-Lunch, 7-Passage, 8-Leave, 9-Bonus, 10-13th Month, 11-Utility,
        12-Other Allowances, 13-NHF, 14-NHIS, 15-National Pension Scheme,
        16-Life Assurance, gross_income, tax_payable
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow(
            [
                "name",
                "tax_payer_number",
                "nationality",
                "designation",
                "no_of_months",
                "1-Basic Salary",
                "2-Housing",
                "3-Transport",
                "4-Furniture",
                "5-Education",
                "6-Lunch",
                "7-Passage",
                "8-Leave",
                "9-Bonus",
                "10-13th Month",
                "11-Utility",
                "12-Other Allowances",
                "13-NHF",
                "14-NHIS",
                "15-National Pension Scheme",
                "16-Life Assurance",
                "gross_income",
                "tax_payable",
            ]
        )

        errors: list[str] = []
        total_tax = Decimal("0")
        employee_count = 0

        for slip in slips:
            employee = slip.employee
            if not employee:
                errors.append(f"Slip {slip.slip_number}: No employee data")
                continue

            # Get employee tax profile
            tax_profile = getattr(employee, "current_tax_profile", None)
            tin = ""
            if tax_profile:
                tin = tax_profile.tin or ""

            # Get earnings breakdown
            earnings = self._extract_earnings_breakdown(slip)

            # Get deductions by component code
            deduction_map = self._extract_deduction_map(slip)
            pension = deduction_map.get("PENSION", Decimal("0"))
            nhf = deduction_map.get("NHF", Decimal("0"))
            nhis = deduction_map.get("NHIS", Decimal("0"))
            paye = deduction_map.get("PAYE", Decimal("0"))

            designation = ""
            if employee.designation:
                designation = employee.designation.designation_name or ""

            writer.writerow(
                [
                    employee.full_name,
                    tin,
                    "Nigerian",  # Default nationality
                    designation,
                    "1",  # Number of months (1 for monthly payroll)
                    str(_round_currency(earnings.get("basic", Decimal("0")))),
                    str(_round_currency(earnings.get("housing", Decimal("0")))),
                    str(_round_currency(earnings.get("transport", Decimal("0")))),
                    str(_round_currency(earnings.get("furniture", Decimal("0")))),
                    str(_round_currency(earnings.get("education", Decimal("0")))),
                    str(_round_currency(earnings.get("lunch", Decimal("0")))),
                    str(_round_currency(earnings.get("passage", Decimal("0")))),
                    str(_round_currency(earnings.get("leave", Decimal("0")))),
                    str(_round_currency(earnings.get("bonus", Decimal("0")))),
                    str(_round_currency(earnings.get("13th_month", Decimal("0")))),
                    str(_round_currency(earnings.get("utility", Decimal("0")))),
                    str(_round_currency(earnings.get("other", Decimal("0")))),
                    str(_round_currency(nhf)),
                    str(_round_currency(nhis)),
                    str(_round_currency(pension)),
                    "0",  # Life Assurance (not tracked separately)
                    str(_round_currency(slip.gross_pay)),
                    str(_round_currency(paye)),
                ]
            )

            total_tax += paye
            employee_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"LIRS_PAYE_{year}_{month:02d}.csv"

        return PAYEExportResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            employee_count=employee_count,
            total_tax=_round_currency(total_tax),
            errors=errors,
        )

    def _generate_fctirs_format(
        self,
        slips: list[SalarySlip],
        year: int,
        month: int,
    ) -> PAYEExportResult:
        """
        Generate FCTIRS (FCT) PAYE format.

        More detailed format with tax band breakdown per NTA 2025.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # FCTIRS has metadata rows before header
        writer.writerow(["FCTIRS PAYE SCHEDULE"])
        writer.writerow([f"Period: {month:02d}/{year}"])
        writer.writerow([])  # Empty row

        # Header row at row 4
        writer.writerow(
            [
                "S/N",
                "Staff ID",
                "Full Name",
                "TIN",
                "Designation",
                "Basic Salary",
                "Housing Allowance",
                "Transport Allowance",
                "Other Allowances",
                "Gross Emolument",
                "Pension (8%)",
                "NHF (2.5%)",
                "NHIS",
                "Total Relief",
                "Taxable Income",
                "First 300K (0%)",
                "Next 300K (15%)",
                "Next 500K (18%)",
                "Next 500K (21%)",
                "Next 1.6M (23%)",
                "Above 3.2M (25%)",
                "Total Tax Due",
                "Monthly Tax",
            ]
        )

        errors: list[str] = []
        total_tax = Decimal("0")
        employee_count = 0

        for idx, slip in enumerate(slips, start=1):
            employee = slip.employee
            if not employee:
                errors.append(f"Slip {slip.slip_number}: No employee data")
                continue

            # Get employee tax profile
            tax_profile = getattr(employee, "current_tax_profile", None)
            tin = ""
            if tax_profile:
                tin = tax_profile.tin or ""

            # Get earnings breakdown
            earnings = self._extract_earnings_breakdown(slip)
            basic = earnings.get("basic", Decimal("0"))
            housing = earnings.get("housing", Decimal("0"))
            transport = earnings.get("transport", Decimal("0"))
            other = slip.gross_pay - basic - housing - transport

            # Get deductions by component code
            deduction_map = self._extract_deduction_map(slip)
            pension = deduction_map.get("PENSION", Decimal("0"))
            nhf = deduction_map.get("NHF", Decimal("0"))
            nhis = deduction_map.get("NHIS", Decimal("0"))
            paye = deduction_map.get("PAYE", Decimal("0"))

            # Calculate annual values for tax band breakdown
            annual_gross = slip.gross_pay * 12
            annual_pension = pension * 12
            annual_nhf = nhf * 12
            annual_nhis = nhis * 12
            total_relief = annual_pension + annual_nhf + annual_nhis

            # Consolidated Relief Allowance (CRA) - NTA 2025
            cra = (
                max(Decimal("200000"), Decimal("0.01") * annual_gross)
                + Decimal("0.20") * annual_gross
            )
            total_relief += cra

            taxable = max(Decimal("0"), annual_gross - total_relief)

            # Tax band breakdown (NTA 2025)
            bands = self._calculate_tax_bands(taxable)
            annual_tax = paye * 12

            designation = ""
            if employee.designation:
                designation = employee.designation.designation_name or ""

                staff_id = employee.employee_code

            writer.writerow(
                [
                    idx,
                    staff_id,
                    employee.full_name,
                    tin,
                    designation,
                    str(_round_currency(basic)),
                    str(_round_currency(housing)),
                    str(_round_currency(transport)),
                    str(_round_currency(other)),
                    str(_round_currency(slip.gross_pay)),
                    str(_round_currency(pension)),
                    str(_round_currency(nhf)),
                    str(_round_currency(nhis)),
                    str(_round_currency(total_relief / 12)),  # Monthly relief
                    str(_round_currency(taxable / 12)),  # Monthly taxable
                    str(_round_currency(bands["band1"])),  # First 300K
                    str(_round_currency(bands["band2"])),  # Next 300K
                    str(_round_currency(bands["band3"])),  # Next 500K
                    str(_round_currency(bands["band4"])),  # Next 500K
                    str(_round_currency(bands["band5"])),  # Next 1.6M
                    str(_round_currency(bands["band6"])),  # Above 3.2M
                    str(_round_currency(annual_tax)),
                    str(_round_currency(paye)),
                ]
            )

            total_tax += paye
            employee_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"FCTIRS_PAYE_{year}_{month:02d}.csv"

        return PAYEExportResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            employee_count=employee_count,
            total_tax=_round_currency(total_tax),
            errors=errors,
        )

    def _extract_earnings_breakdown(self, slip: SalarySlip) -> dict[str, Decimal]:
        """Extract earnings breakdown from salary slip."""
        breakdown: dict[str, Decimal] = {
            "basic": Decimal("0"),
            "housing": Decimal("0"),
            "transport": Decimal("0"),
            "furniture": Decimal("0"),
            "education": Decimal("0"),
            "lunch": Decimal("0"),
            "passage": Decimal("0"),
            "leave": Decimal("0"),
            "bonus": Decimal("0"),
            "13th_month": Decimal("0"),
            "utility": Decimal("0"),
            "other": Decimal("0"),
        }

        # Map component codes to breakdown keys
        code_mapping = {
            "BASIC": "basic",
            "HOUSING": "housing",
            "TRANSPORT": "transport",
            "FURNITURE": "furniture",
            "EDUCATION": "education",
            "LUNCH": "lunch",
            "MEAL": "lunch",
            "PASSAGE": "passage",
            "LEAVE": "leave",
            "LEAVE_ALLOWANCE": "leave",
            "BONUS": "bonus",
            "13TH_MONTH": "13th_month",
            "THIRTEENTH_MONTH": "13th_month",
            "UTILITY": "utility",
        }

        for earning in slip.earnings:
            component_code = (
                earning.component.component_code if earning.component else ""
            ).upper()
            key = code_mapping.get(component_code, "other")
            breakdown[key] += earning.amount or Decimal("0")

        return breakdown

    def _extract_deduction_map(self, slip: SalarySlip) -> dict[str, Decimal]:
        """Map deduction component codes to totals."""
        totals: dict[str, Decimal] = {}
        for deduction in slip.deductions:
            component_code = (
                deduction.component.component_code if deduction.component else ""
            ).upper()
            if not component_code:
                continue
            totals[component_code] = totals.get(component_code, Decimal("0")) + (
                deduction.amount or Decimal("0")
            )
        return totals

    def _calculate_tax_bands(self, taxable_income: Decimal) -> dict[str, Decimal]:
        """
        Calculate tax amounts per NTA 2025 bands.

        Returns tax amount (not income) in each band.
        """
        bands = {
            "band1": Decimal("0"),  # First 300K @ 0%
            "band2": Decimal("0"),  # Next 300K @ 15%
            "band3": Decimal("0"),  # Next 500K @ 18%
            "band4": Decimal("0"),  # Next 500K @ 21%
            "band5": Decimal("0"),  # Next 1.6M @ 23%
            "band6": Decimal("0"),  # Above 3.2M @ 25%
        }

        remaining = taxable_income

        # Band 1: First 300,000 @ 0%
        band1_limit = Decimal("300000")
        if remaining <= band1_limit:
            return bands
        remaining -= band1_limit

        # Band 2: Next 300,000 @ 15%
        band2_limit = Decimal("300000")
        band2_income = min(remaining, band2_limit)
        bands["band2"] = band2_income * Decimal("0.15")
        if remaining <= band2_limit:
            return bands
        remaining -= band2_limit

        # Band 3: Next 500,000 @ 18%
        band3_limit = Decimal("500000")
        band3_income = min(remaining, band3_limit)
        bands["band3"] = band3_income * Decimal("0.18")
        if remaining <= band3_limit:
            return bands
        remaining -= band3_limit

        # Band 4: Next 500,000 @ 21%
        band4_limit = Decimal("500000")
        band4_income = min(remaining, band4_limit)
        bands["band4"] = band4_income * Decimal("0.21")
        if remaining <= band4_limit:
            return bands
        remaining -= band4_limit

        # Band 5: Next 1,600,000 @ 23%
        band5_limit = Decimal("1600000")
        band5_income = min(remaining, band5_limit)
        bands["band5"] = band5_income * Decimal("0.23")
        if remaining <= band5_limit:
            return bands
        remaining -= band5_limit

        # Band 6: Above 3,200,000 @ 25%
        bands["band6"] = remaining * Decimal("0.25")

        return bands


def paye_export_service(db: Session) -> PAYEExportService:
    """Create a PAYEExportService instance."""
    return PAYEExportService(db)
