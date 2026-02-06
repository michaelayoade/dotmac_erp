"""
Tax Calculator Protocol - Abstract interface for payroll tax calculation.

This module defines the protocol (interface) for tax calculators, enabling:
- Multiple tax regimes (Nigeria PAYE, Kenya PAYE, US withholding, etc.)
- Dependency injection for testing with mock calculators
- Future extensibility without changing consuming code

The PAYECalculator (NTA 2025) implements this protocol for Nigerian taxes.

Usage:
    # Production - use real calculator
    calculator = PAYECalculator(db)
    slip_service = SalarySlipService(db, tax_calculator=calculator)

    # Testing - use mock calculator
    mock_calculator = MockTaxCalculator(fixed_tax=Decimal("5000"))
    slip_service = SalarySlipService(db, tax_calculator=mock_calculator)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tax Result - Base class for calculation results
# ---------------------------------------------------------------------------


@dataclass
class TaxResult:
    """
    Base class for tax calculation results.

    Provides the minimal set of fields that all tax calculators must return.
    Specific implementations (like PAYEBreakdown) can extend this with
    additional fields for their jurisdiction.
    """

    # Core tax amounts (monthly)
    monthly_tax: Decimal
    monthly_pension: Decimal = Decimal("0")
    monthly_employer_pension: Decimal = Decimal("0")
    monthly_social_security: Decimal = Decimal("0")  # Generic social security
    monthly_health_insurance: Decimal = Decimal("0")

    # Annual amounts for reference
    annual_tax: Decimal = Decimal("0")
    taxable_income: Decimal = Decimal("0")

    # Effective tax rate
    effective_rate: Decimal = Decimal("0")

    # Tax exemption status
    is_tax_exempt: bool = False

    # Employee reference
    employee_id: Optional[UUID] = None

    @property
    def total_monthly_deductions(self) -> Decimal:
        """Total of all monthly tax-related deductions."""
        return (
            self.monthly_tax
            + self.monthly_pension
            + self.monthly_social_security
            + self.monthly_health_insurance
        )

    @property
    def total_monthly_employer_contributions(self) -> Decimal:
        """Total employer contributions (not deducted from employee)."""
        return self.monthly_employer_pension


# ---------------------------------------------------------------------------
# Tax Calculator Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TaxCalculator(Protocol):
    """
    Protocol defining the interface for tax calculators.

    Any tax calculator implementation must provide a `calculate` method
    that accepts gross/basic salaries and returns a TaxResult (or subclass).

    This is a structural protocol - implementations don't need to explicitly
    inherit from it, they just need to have matching method signatures.

    Example implementations:
    - PAYECalculator (Nigeria NTA 2025)
    - KenyaPAYECalculator (Kenya tax rules)
    - MockTaxCalculator (for testing)
    """

    def calculate(
        self,
        organization_id: UUID,
        gross_monthly: Decimal,
        basic_monthly: Decimal,
        employee_id: Optional[UUID] = None,
        as_of_date: Optional[date] = None,
        **kwargs,
    ) -> TaxResult:
        """
        Calculate tax for given income.

        Args:
            organization_id: Organization for tax configuration lookup
            gross_monthly: Monthly gross salary
            basic_monthly: Monthly basic salary (for statutory deductions)
            employee_id: Optional employee ID for profile lookup
            as_of_date: Date for tax rule lookup (default: today)
            **kwargs: Implementation-specific parameters

        Returns:
            TaxResult (or subclass) with calculation details
        """
        ...


# ---------------------------------------------------------------------------
# Null/Zero Tax Calculator
# ---------------------------------------------------------------------------


class ZeroTaxCalculator:
    """
    Tax calculator that returns zero tax.

    Useful for:
    - Contract workers exempt from tax withholding
    - Testing scenarios where tax calculation should be skipped
    - Jurisdictions without income tax
    """

    def calculate(
        self,
        organization_id: UUID,
        gross_monthly: Decimal,
        basic_monthly: Decimal,
        employee_id: Optional[UUID] = None,
        as_of_date: Optional[date] = None,
        **kwargs,
    ) -> TaxResult:
        """Return zero tax result."""
        return TaxResult(
            monthly_tax=Decimal("0"),
            monthly_pension=Decimal("0"),
            monthly_employer_pension=Decimal("0"),
            monthly_social_security=Decimal("0"),
            monthly_health_insurance=Decimal("0"),
            annual_tax=Decimal("0"),
            taxable_income=gross_monthly * Decimal("12"),
            effective_rate=Decimal("0"),
            is_tax_exempt=True,
            employee_id=employee_id,
        )


# ---------------------------------------------------------------------------
# Fixed Tax Calculator (for testing)
# ---------------------------------------------------------------------------


class FixedTaxCalculator:
    """
    Tax calculator that returns a fixed tax amount.

    Useful for testing scenarios where you want predictable tax amounts
    without the complexity of real tax band calculations.

    Usage:
        calculator = FixedTaxCalculator(
            monthly_tax=Decimal("5000"),
            monthly_pension=Decimal("2000"),
        )
        result = calculator.calculate(org_id, gross, basic)
        assert result.monthly_tax == Decimal("5000")
    """

    def __init__(
        self,
        monthly_tax: Decimal = Decimal("0"),
        monthly_pension: Decimal = Decimal("0"),
        monthly_employer_pension: Decimal = Decimal("0"),
        monthly_social_security: Decimal = Decimal("0"),
        monthly_health_insurance: Decimal = Decimal("0"),
        effective_rate: Decimal = Decimal("0"),
    ):
        self._monthly_tax = monthly_tax
        self._monthly_pension = monthly_pension
        self._monthly_employer_pension = monthly_employer_pension
        self._monthly_social_security = monthly_social_security
        self._monthly_health_insurance = monthly_health_insurance
        self._effective_rate = effective_rate

    def calculate(
        self,
        organization_id: UUID,
        gross_monthly: Decimal,
        basic_monthly: Decimal,
        employee_id: Optional[UUID] = None,
        as_of_date: Optional[date] = None,
        **kwargs,
    ) -> TaxResult:
        """Return fixed tax amounts."""
        annual_gross = gross_monthly * Decimal("12")
        return TaxResult(
            monthly_tax=self._monthly_tax,
            monthly_pension=self._monthly_pension,
            monthly_employer_pension=self._monthly_employer_pension,
            monthly_social_security=self._monthly_social_security,
            monthly_health_insurance=self._monthly_health_insurance,
            annual_tax=self._monthly_tax * Decimal("12"),
            taxable_income=annual_gross,
            effective_rate=self._effective_rate,
            is_tax_exempt=False,
            employee_id=employee_id,
        )


# ---------------------------------------------------------------------------
# Percentage Tax Calculator (for testing)
# ---------------------------------------------------------------------------


class PercentageTaxCalculator:
    """
    Tax calculator that applies a flat percentage.

    Useful for testing or simple tax regimes with flat rates.

    Usage:
        calculator = PercentageTaxCalculator(tax_rate=Decimal("0.20"))
        result = calculator.calculate(org_id, Decimal("100000"), Decimal("60000"))
        assert result.monthly_tax == Decimal("20000")  # 20% of gross
    """

    def __init__(
        self,
        tax_rate: Decimal = Decimal("0.10"),
        pension_rate: Decimal = Decimal("0.08"),
        employer_pension_rate: Decimal = Decimal("0.10"),
    ):
        self._tax_rate = tax_rate
        self._pension_rate = pension_rate
        self._employer_pension_rate = employer_pension_rate

    def calculate(
        self,
        organization_id: UUID,
        gross_monthly: Decimal,
        basic_monthly: Decimal,
        employee_id: Optional[UUID] = None,
        as_of_date: Optional[date] = None,
        **kwargs,
    ) -> TaxResult:
        """Calculate tax as percentage of gross."""
        monthly_tax = gross_monthly * self._tax_rate
        monthly_pension = basic_monthly * self._pension_rate
        monthly_employer_pension = basic_monthly * self._employer_pension_rate

        return TaxResult(
            monthly_tax=monthly_tax,
            monthly_pension=monthly_pension,
            monthly_employer_pension=monthly_employer_pension,
            monthly_social_security=Decimal("0"),
            monthly_health_insurance=Decimal("0"),
            annual_tax=monthly_tax * Decimal("12"),
            taxable_income=gross_monthly * Decimal("12"),
            effective_rate=self._tax_rate,
            is_tax_exempt=False,
            employee_id=employee_id,
        )


# ---------------------------------------------------------------------------
# Type checking helper
# ---------------------------------------------------------------------------


def is_tax_calculator(obj: object) -> bool:
    """Check if an object implements the TaxCalculator protocol."""
    return isinstance(obj, TaxCalculator)
