"""
Tax Calculation Service.

Centralized tax calculation for AR and AP invoices.
Handles multiple taxes per line, compound taxes, and inclusive taxes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.tax.tax_code import TaxCode
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


@dataclass
class LineTaxInput:
    """Input for calculating taxes on a single line."""

    line_amount: Decimal
    tax_code_ids: list[UUID]
    transaction_date: date


@dataclass
class LineTaxResult:
    """Result of tax calculation for a single tax code on a line."""

    tax_code_id: UUID
    tax_code: str
    tax_name: str
    base_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    is_inclusive: bool
    is_recoverable: bool
    recoverable_amount: Decimal
    non_recoverable_amount: Decimal
    sequence: int


@dataclass
class LineCalculationResult:
    """Complete tax calculation result for a line."""

    net_amount: Decimal  # Amount before all taxes
    taxes: list[LineTaxResult] = field(default_factory=list)
    total_tax: Decimal = Decimal("0")
    gross_amount: Decimal = Decimal("0")  # Amount including all taxes


@dataclass
class InvoiceLineTaxInput:
    """Input for a single invoice line with multiple taxes."""

    line_id: UUID | None
    line_amount: Decimal
    tax_code_ids: list[UUID]


@dataclass
class InvoiceTaxResult:
    """Tax calculation result for an entire invoice."""

    lines: list[LineCalculationResult] = field(default_factory=list)
    total_tax: Decimal = Decimal("0")
    total_net: Decimal = Decimal("0")
    total_gross: Decimal = Decimal("0")


class TaxCalculationService:
    """
    Centralized tax calculation for AR and AP.

    Handles:
    - Multiple tax codes per invoice line
    - Compound taxes (tax on tax)
    - Inclusive taxes (tax included in price)
    - Effective date filtering
    - Recoverability for input tax
    """

    @staticmethod
    def get_effective_tax_code(
        db: Session,
        organization_id: UUID,
        tax_code_id: UUID,
        transaction_date: date,
    ) -> TaxCode:
        """
        Get tax code if effective on the given date.

        Args:
            db: Database session
            organization_id: Organization scope
            tax_code_id: Tax code to lookup
            transaction_date: Date for effectiveness check

        Returns:
            TaxCode if valid and effective

        Raises:
            HTTPException if tax code is invalid, inactive, or not effective
        """
        org_id = coerce_uuid(organization_id)
        tc_id = coerce_uuid(tax_code_id)

        tax_code = db.get(TaxCode, tc_id)
        if not tax_code or tax_code.organization_id != org_id:
            raise HTTPException(status_code=404, detail=f"Tax code {tc_id} not found")

        if not tax_code.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"Tax code '{tax_code.tax_code}' is not active",
            )

        if transaction_date < tax_code.effective_from:
            raise HTTPException(
                status_code=400,
                detail=f"Tax code '{tax_code.tax_code}' is not yet effective on {transaction_date}",
            )

        if tax_code.effective_to and transaction_date > tax_code.effective_to:
            raise HTTPException(
                status_code=400,
                detail=f"Tax code '{tax_code.tax_code}' has expired as of {tax_code.effective_to}",
            )

        return tax_code

    @staticmethod
    def calculate_single_tax(
        base_amount: Decimal,
        tax_code: TaxCode,
    ) -> tuple[Decimal, Decimal]:
        """
        Calculate tax for a single tax code.

        Args:
            base_amount: Amount to calculate tax on
            tax_code: Tax code with rate and settings

        Returns:
            Tuple of (net_base, tax_amount)
        """
        if tax_code.is_inclusive:
            # Tax is included in the price - extract it
            # formula: tax = base * rate / (1 + rate)
            divisor = Decimal("1") + tax_code.tax_rate
            tax_amount = (base_amount * tax_code.tax_rate / divisor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            net_base = base_amount - tax_amount
        else:
            # Tax is additional - straightforward calculation
            net_base = base_amount
            tax_amount = (base_amount * tax_code.tax_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        return net_base, tax_amount

    @classmethod
    def calculate_line_taxes(
        cls,
        db: Session,
        organization_id: UUID,
        line_amount: Decimal,
        tax_code_ids: list[UUID],
        transaction_date: date,
    ) -> LineCalculationResult:
        """
        Calculate all taxes for a single invoice line.

        Handles:
        - Multiple tax codes per line
        - Compound taxes (sequence ordering)
        - Inclusive taxes

        Args:
            db: Database session
            organization_id: Organization scope
            line_amount: Amount to calculate tax on
            tax_code_ids: List of tax code IDs to apply
            transaction_date: Date for rate lookup and effectiveness

        Returns:
            LineCalculationResult with all tax details
        """
        org_id = coerce_uuid(organization_id)

        if not tax_code_ids:
            # No taxes - return simple result
            return LineCalculationResult(
                net_amount=line_amount,
                taxes=[],
                total_tax=Decimal("0"),
                gross_amount=line_amount,
            )

        # Get all tax codes and validate them
        tax_codes: list[TaxCode] = []
        for tc_id in tax_code_ids:
            tax_code = cls.get_effective_tax_code(db, org_id, tc_id, transaction_date)
            tax_codes.append(tax_code)

        # Sort by compound flag - non-compound first, then compound
        # This ensures base taxes are calculated before compound taxes
        tax_codes.sort(key=lambda tc: (tc.is_compound, tc.tax_code))

        # First pass: handle inclusive taxes
        # If any tax is inclusive, we need to extract it from the line amount first
        inclusive_taxes = [tc for tc in tax_codes if tc.is_inclusive]
        exclusive_taxes = [tc for tc in tax_codes if not tc.is_inclusive]

        running_base = line_amount
        taxes: list[LineTaxResult] = []
        sequence = 1

        # Process inclusive taxes first (extract from price)
        for tax_code in inclusive_taxes:
            net_base, tax_amount = cls.calculate_single_tax(running_base, tax_code)

            # Calculate recoverability
            if tax_code.is_recoverable:
                recoverable = (tax_amount * tax_code.recovery_rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                recoverable = Decimal("0")
            non_recoverable = tax_amount - recoverable

            taxes.append(
                LineTaxResult(
                    tax_code_id=tax_code.tax_code_id,
                    tax_code=tax_code.tax_code,
                    tax_name=tax_code.tax_name,
                    base_amount=running_base,
                    tax_rate=tax_code.tax_rate,
                    tax_amount=tax_amount,
                    is_inclusive=True,
                    is_recoverable=tax_code.is_recoverable,
                    recoverable_amount=recoverable,
                    non_recoverable_amount=non_recoverable,
                    sequence=sequence,
                )
            )
            sequence += 1

            # Update running base (extract tax from total)
            running_base = net_base

        net_amount = (
            running_base  # This is the net amount after extracting inclusive taxes
        )

        # Process exclusive taxes (add on top)
        compound_base = net_amount
        for tax_code in exclusive_taxes:
            if tax_code.is_compound:
                # Compound tax: calculate on net + previous taxes
                compound_base = net_amount + sum(t.tax_amount for t in taxes)

            base_for_calc = compound_base if tax_code.is_compound else net_amount
            _, tax_amount = cls.calculate_single_tax(base_for_calc, tax_code)

            # Calculate recoverability
            if tax_code.is_recoverable:
                recoverable = (tax_amount * tax_code.recovery_rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                recoverable = Decimal("0")
            non_recoverable = tax_amount - recoverable

            taxes.append(
                LineTaxResult(
                    tax_code_id=tax_code.tax_code_id,
                    tax_code=tax_code.tax_code,
                    tax_name=tax_code.tax_name,
                    base_amount=base_for_calc,
                    tax_rate=tax_code.tax_rate,
                    tax_amount=tax_amount,
                    is_inclusive=False,
                    is_recoverable=tax_code.is_recoverable,
                    recoverable_amount=recoverable,
                    non_recoverable_amount=non_recoverable,
                    sequence=sequence,
                )
            )
            sequence += 1

        total_tax = sum((t.tax_amount for t in taxes), Decimal("0"))
        gross_amount = net_amount + sum(
            (t.tax_amount for t in taxes if not t.is_inclusive),
            Decimal("0"),
        )

        # For inclusive taxes, the gross_amount stays the same as line_amount
        if inclusive_taxes and not exclusive_taxes:
            gross_amount = line_amount

        return LineCalculationResult(
            net_amount=net_amount,
            taxes=taxes,
            total_tax=total_tax,
            gross_amount=gross_amount,
        )

    @classmethod
    def calculate_invoice_taxes(
        cls,
        db: Session,
        organization_id: UUID,
        lines: list[InvoiceLineTaxInput],
        transaction_date: date,
    ) -> InvoiceTaxResult:
        """
        Calculate taxes for an entire invoice.

        Args:
            db: Database session
            organization_id: Organization scope
            lines: List of line inputs with amounts and tax codes
            transaction_date: Date for rate lookup

        Returns:
            InvoiceTaxResult with totals and per-line breakdown
        """
        line_results: list[LineCalculationResult] = []
        total_tax = Decimal("0")
        total_net = Decimal("0")
        total_gross = Decimal("0")

        for line_input in lines:
            line_result = cls.calculate_line_taxes(
                db=db,
                organization_id=organization_id,
                line_amount=line_input.line_amount,
                tax_code_ids=line_input.tax_code_ids,
                transaction_date=transaction_date,
            )
            line_results.append(line_result)

            total_tax += line_result.total_tax
            total_net += line_result.net_amount
            total_gross += line_result.gross_amount

        return InvoiceTaxResult(
            lines=line_results,
            total_tax=total_tax,
            total_net=total_net,
            total_gross=total_gross,
        )

    @classmethod
    def calculate_wht(
        cls,
        db: Session,
        organization_id: UUID,
        base_amount: Decimal,
        wht_code_id: UUID,
        transaction_date: date,
    ) -> tuple[Decimal, Decimal]:
        """
        Calculate withholding tax.

        Args:
            db: Database session
            organization_id: Organization scope
            base_amount: Amount to calculate WHT on (typically invoice gross)
            wht_code_id: WHT tax code ID
            transaction_date: Date for rate lookup

        Returns:
            Tuple of (wht_amount, net_amount_received)
        """
        org_id = coerce_uuid(organization_id)
        tax_code = cls.get_effective_tax_code(db, org_id, wht_code_id, transaction_date)

        # WHT is calculated as a percentage of the base.
        wht_amount = (base_amount * tax_code.tax_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        net_received = base_amount - wht_amount

        return wht_amount, net_received


# Module-level singleton instance
tax_calculation_service = TaxCalculationService()
