"""
NHF Export Service.

Generates NHF (National Housing Fund) contribution schedules for FMBN.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.payroll.salary_slip import (
    SalarySlip,
    SalarySlipStatus,
    SalarySlipDeduction,
)

logger = logging.getLogger(__name__)


def _round_currency(value: Decimal) -> Decimal:
    """Round to 2 decimal places using ROUND_HALF_UP."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class NHFExportResult:
    """Result of NHF export generation."""

    content: bytes
    filename: str
    content_type: str
    employee_count: int
    total_contribution: Decimal
    errors: list[str]


class NHFExportService:
    """
    Service for generating NHF contribution schedules.

    Generates files for uploading to FMBN (Federal Mortgage Bank of Nigeria).
    """

    def __init__(self, db: Session):
        self.db = db

    def generate_export(
        self,
        organization_id: UUID,
        year: int,
        month: int,
        entry_id: Optional[UUID] = None,
    ) -> NHFExportResult:
        """
        Generate NHF export for a period.

        Args:
            organization_id: Organization ID
            year: Contribution year
            month: Contribution month
            entry_id: Optional payroll entry ID to filter by

        Returns:
            NHFExportResult with file content and metadata
        """
        slips = self._get_slips(organization_id, year, month, entry_id)
        return self._generate_fmbn_format(slips, year, month)

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

    def _split_name(self, full_name: str) -> tuple[str, str, str]:
        """
        Split full name into last, first, and middle names.

        Assumes format: "First Middle Last" or "First Last"
        FMBN requires: LASTNAME, FIRSTNAME, MIDDLE_NAME
        """
        parts = full_name.strip().split()

        if len(parts) == 0:
            return ("", "", "")
        elif len(parts) == 1:
            return (parts[0], "", "")
        elif len(parts) == 2:
            # Assume "First Last"
            return (parts[1], parts[0], "")
        else:
            # Assume "First Middle... Last"
            return (parts[-1], parts[0], " ".join(parts[1:-1]))

    def _generate_fmbn_format(
        self,
        slips: list[SalarySlip],
        year: int,
        month: int,
    ) -> NHFExportResult:
        """
        Generate FMBN format for NHF contributions.

        FMBN standard format:
        - LASTNAME
        - FIRSTNAME
        - MIDDLE_NAME
        - NHF_NO
        - AMOUNT
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            "LASTNAME",
            "FIRSTNAME",
            "MIDDLE_NAME",
            "NHF_NO",
            "AMOUNT",
        ])

        errors: list[str] = []
        total_contribution = Decimal("0")
        employee_count = 0

        for slip in slips:
            employee = slip.employee
            if not employee:
                errors.append(f"Slip {slip.slip_number}: No employee data")
                continue

            deduction_map = self._extract_deduction_map(slip)
            nhf = deduction_map.get("NHF", Decimal("0"))

            # Skip if no NHF contribution
            if nhf == 0:
                continue

            # Get tax profile for NHF number
            tax_profile = getattr(employee, "current_tax_profile", None)
            nhf_number = ""

            if tax_profile:
                nhf_number = tax_profile.nhf_number or ""

            # Track missing data
            if not nhf_number:
                errors.append(f"{employee.full_name}: Missing NHF Number")

            # Split name
            last_name, first_name, middle_name = self._split_name(employee.full_name)

            writer.writerow([
                last_name.upper(),
                first_name.upper(),
                middle_name.upper(),
                nhf_number,
                str(_round_currency(nhf)),
            ])

            total_contribution += nhf
            employee_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"NHF_FMBN_{year}_{month:02d}.csv"

        return NHFExportResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            employee_count=employee_count,
            total_contribution=_round_currency(total_contribution),
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


def nhf_export_service(db: Session) -> NHFExportService:
    """Create a NHFExportService instance."""
    return NHFExportService(db)
