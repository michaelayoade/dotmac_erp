"""
Tests for Tax Calculator Protocol and implementations.

Verifies:
- Protocol compliance for all calculator implementations
- TaxResult base class functionality
- Mock calculators for testing scenarios
- PAYECalculator implements TaxCalculator protocol
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.people.payroll.paye_calculator import (
    PAYEBreakdown,
    PAYECalculator,
)
from app.services.people.payroll.tax_calculator import (
    FixedTaxCalculator,
    PercentageTaxCalculator,
    TaxCalculator,
    TaxResult,
    ZeroTaxCalculator,
    is_tax_calculator,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_id():
    """Test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def employee_id():
    """Test employee ID."""
    return uuid.uuid4()


@pytest.fixture
def gross_monthly():
    """Standard monthly gross salary for tests."""
    return Decimal("500000")


@pytest.fixture
def basic_monthly():
    """Standard monthly basic salary for tests."""
    return Decimal("300000")


# ---------------------------------------------------------------------------
# TaxResult Tests
# ---------------------------------------------------------------------------


class TestTaxResult:
    """Tests for TaxResult base class."""

    def test_tax_result_defaults(self):
        """TaxResult should have sensible defaults."""
        result = TaxResult(monthly_tax=Decimal("5000"))

        assert result.monthly_tax == Decimal("5000")
        assert result.monthly_pension == Decimal("0")
        assert result.monthly_employer_pension == Decimal("0")
        assert result.monthly_social_security == Decimal("0")
        assert result.monthly_health_insurance == Decimal("0")
        assert result.annual_tax == Decimal("0")
        assert result.effective_rate == Decimal("0")
        assert result.is_tax_exempt is False
        assert result.employee_id is None

    def test_total_monthly_deductions(self):
        """Should calculate total monthly deductions correctly."""
        result = TaxResult(
            monthly_tax=Decimal("10000"),
            monthly_pension=Decimal("5000"),
            monthly_social_security=Decimal("2000"),
            monthly_health_insurance=Decimal("1000"),
        )

        assert result.total_monthly_deductions == Decimal("18000")

    def test_total_employer_contributions(self):
        """Should calculate employer contributions correctly."""
        result = TaxResult(
            monthly_tax=Decimal("10000"),
            monthly_employer_pension=Decimal("6250"),
        )

        assert result.total_monthly_employer_contributions == Decimal("6250")


# ---------------------------------------------------------------------------
# Protocol Compliance Tests
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Tests verifying TaxCalculator protocol compliance."""

    def test_zero_tax_calculator_implements_protocol(self):
        """ZeroTaxCalculator should implement TaxCalculator protocol."""
        calculator = ZeroTaxCalculator()
        assert is_tax_calculator(calculator)
        assert isinstance(calculator, TaxCalculator)

    def test_fixed_tax_calculator_implements_protocol(self):
        """FixedTaxCalculator should implement TaxCalculator protocol."""
        calculator = FixedTaxCalculator()
        assert is_tax_calculator(calculator)
        assert isinstance(calculator, TaxCalculator)

    def test_percentage_tax_calculator_implements_protocol(self):
        """PercentageTaxCalculator should implement TaxCalculator protocol."""
        calculator = PercentageTaxCalculator()
        assert is_tax_calculator(calculator)
        assert isinstance(calculator, TaxCalculator)

    def test_paye_calculator_implements_protocol(self):
        """PAYECalculator should implement TaxCalculator protocol."""
        mock_db = MagicMock()
        calculator = PAYECalculator(mock_db)
        assert is_tax_calculator(calculator)
        assert isinstance(calculator, TaxCalculator)

    def test_paye_breakdown_duck_types_as_tax_result(self):
        """PAYEBreakdown should have all TaxResult interface methods/properties."""
        # PAYEBreakdown uses duck typing, not inheritance
        # Verify it has the required interface
        required_attrs = [
            "monthly_tax",
            "monthly_pension",
            "monthly_employer_pension",
            "monthly_social_security",
            "monthly_health_insurance",
            "annual_tax",
            "taxable_income",
            "effective_rate",
            "is_tax_exempt",
            "employee_id",
            "total_monthly_deductions",
        ]
        for attr in required_attrs:
            assert (
                hasattr(PAYEBreakdown, attr)
                or attr in PAYEBreakdown.__dataclass_fields__
            )

    def test_arbitrary_object_not_tax_calculator(self):
        """Random objects should not be TaxCalculator."""
        assert not is_tax_calculator("string")
        assert not is_tax_calculator(123)
        assert not is_tax_calculator({})
        assert not is_tax_calculator(MagicMock())  # MagicMock without calculate


# ---------------------------------------------------------------------------
# ZeroTaxCalculator Tests
# ---------------------------------------------------------------------------


class TestZeroTaxCalculator:
    """Tests for ZeroTaxCalculator."""

    def test_returns_zero_tax(self, org_id, gross_monthly, basic_monthly):
        """Should return zero tax amounts."""
        calculator = ZeroTaxCalculator()
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        assert result.monthly_tax == Decimal("0")
        assert result.monthly_pension == Decimal("0")
        assert result.annual_tax == Decimal("0")
        assert result.effective_rate == Decimal("0")

    def test_marks_as_tax_exempt(self, org_id, gross_monthly, basic_monthly):
        """Should mark result as tax exempt."""
        calculator = ZeroTaxCalculator()
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        assert result.is_tax_exempt is True

    def test_calculates_taxable_income(self, org_id, gross_monthly, basic_monthly):
        """Should still calculate taxable income for reference."""
        calculator = ZeroTaxCalculator()
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        expected_annual = gross_monthly * Decimal("12")
        assert result.taxable_income == expected_annual

    def test_preserves_employee_id(
        self, org_id, gross_monthly, basic_monthly, employee_id
    ):
        """Should preserve employee_id in result."""
        calculator = ZeroTaxCalculator()
        result = calculator.calculate(
            org_id, gross_monthly, basic_monthly, employee_id=employee_id
        )

        assert result.employee_id == employee_id


# ---------------------------------------------------------------------------
# FixedTaxCalculator Tests
# ---------------------------------------------------------------------------


class TestFixedTaxCalculator:
    """Tests for FixedTaxCalculator."""

    def test_returns_fixed_amounts(self, org_id, gross_monthly, basic_monthly):
        """Should return configured fixed amounts."""
        calculator = FixedTaxCalculator(
            monthly_tax=Decimal("15000"),
            monthly_pension=Decimal("8000"),
            monthly_employer_pension=Decimal("10000"),
        )
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        assert result.monthly_tax == Decimal("15000")
        assert result.monthly_pension == Decimal("8000")
        assert result.monthly_employer_pension == Decimal("10000")

    def test_default_values(self, org_id, gross_monthly, basic_monthly):
        """Should use zero defaults if not configured."""
        calculator = FixedTaxCalculator()
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        assert result.monthly_tax == Decimal("0")
        assert result.monthly_pension == Decimal("0")

    def test_calculates_annual_tax(self, org_id, gross_monthly, basic_monthly):
        """Should calculate annual tax from monthly."""
        calculator = FixedTaxCalculator(monthly_tax=Decimal("5000"))
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        assert result.annual_tax == Decimal("60000")  # 5000 * 12

    def test_not_tax_exempt(self, org_id, gross_monthly, basic_monthly):
        """Fixed calculator results should not be marked exempt."""
        calculator = FixedTaxCalculator(monthly_tax=Decimal("5000"))
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        assert result.is_tax_exempt is False


# ---------------------------------------------------------------------------
# PercentageTaxCalculator Tests
# ---------------------------------------------------------------------------


class TestPercentageTaxCalculator:
    """Tests for PercentageTaxCalculator."""

    def test_calculates_percentage_of_gross(self, org_id, gross_monthly, basic_monthly):
        """Should calculate tax as percentage of gross."""
        calculator = PercentageTaxCalculator(tax_rate=Decimal("0.20"))
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        expected_tax = gross_monthly * Decimal("0.20")
        assert result.monthly_tax == expected_tax

    def test_calculates_pension_from_basic(self, org_id, gross_monthly, basic_monthly):
        """Should calculate pension as percentage of basic."""
        calculator = PercentageTaxCalculator(
            tax_rate=Decimal("0.10"),
            pension_rate=Decimal("0.08"),
            employer_pension_rate=Decimal("0.10"),
        )
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        expected_pension = basic_monthly * Decimal("0.08")
        expected_employer_pension = basic_monthly * Decimal("0.10")

        assert result.monthly_pension == expected_pension
        assert result.monthly_employer_pension == expected_employer_pension

    def test_effective_rate_matches_configured_rate(
        self, org_id, gross_monthly, basic_monthly
    ):
        """Effective rate should match the configured tax rate."""
        calculator = PercentageTaxCalculator(tax_rate=Decimal("0.15"))
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        assert result.effective_rate == Decimal("0.15")

    def test_default_rates(self, org_id, gross_monthly, basic_monthly):
        """Should have sensible default rates."""
        calculator = PercentageTaxCalculator()  # Uses defaults
        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        # Default tax rate is 10%
        expected_tax = gross_monthly * Decimal("0.10")
        assert result.monthly_tax == expected_tax


# ---------------------------------------------------------------------------
# PAYEBreakdown Compatibility Tests
# ---------------------------------------------------------------------------


class TestPAYEBreakdownCompatibility:
    """Tests verifying PAYEBreakdown works with TaxResult interface."""

    def _create_breakdown(self, **overrides) -> PAYEBreakdown:
        """Helper to create PAYEBreakdown with required fields."""
        defaults = {
            "annual_gross": Decimal("6000000"),
            "annual_basic": Decimal("3600000"),
            "annual_rent": Decimal("0"),
            "pension_amount": Decimal("288000"),
            "pension_rate": Decimal("0.08"),
            "employer_pension_amount": Decimal("360000"),
            "employer_pension_rate": Decimal("0.10"),
            "nhf_amount": Decimal("90000"),
            "nhf_rate": Decimal("0.025"),
            "nhis_amount": Decimal("0"),
            "nhis_rate": Decimal("0"),
            "total_statutory": Decimal("378000"),
            "rent_relief": Decimal("0"),
            "taxable_income": Decimal("5622000"),
        }
        defaults.update(overrides)
        return PAYEBreakdown(**defaults)

    def test_paye_breakdown_has_tax_result_fields(self):
        """PAYEBreakdown should have all TaxResult fields."""
        breakdown = self._create_breakdown(
            monthly_tax=Decimal("50000"),
            monthly_pension=Decimal("24000"),
            monthly_employer_pension=Decimal("30000"),
            annual_tax=Decimal("600000"),
            taxable_income=Decimal("5400000"),
            effective_rate=Decimal("0.10"),
            is_tax_exempt=False,
        )

        # TaxResult interface
        assert breakdown.monthly_tax == Decimal("50000")
        assert breakdown.monthly_pension == Decimal("24000")
        assert breakdown.monthly_employer_pension == Decimal("30000")
        assert breakdown.annual_tax == Decimal("600000")
        assert breakdown.taxable_income == Decimal("5400000")
        assert breakdown.effective_rate == Decimal("0.10")
        assert breakdown.is_tax_exempt is False

    def test_paye_breakdown_social_security_maps_to_nhf(self):
        """monthly_social_security should map to monthly_nhf."""
        breakdown = self._create_breakdown(
            monthly_nhf=Decimal("7500"),
        )

        assert breakdown.monthly_social_security == Decimal("7500")

    def test_paye_breakdown_health_insurance_maps_to_nhis(self):
        """monthly_health_insurance should map to monthly_nhis."""
        breakdown = self._create_breakdown(
            monthly_nhis=Decimal("5000"),
        )

        assert breakdown.monthly_health_insurance == Decimal("5000")

    def test_paye_breakdown_total_deductions(self):
        """total_monthly_deductions should include NHF and NHIS."""
        breakdown = self._create_breakdown(
            monthly_tax=Decimal("50000"),
            monthly_pension=Decimal("24000"),
            monthly_nhf=Decimal("7500"),
            monthly_nhis=Decimal("5000"),
        )

        # total = tax + pension + nhf + nhis
        expected = (
            Decimal("50000") + Decimal("24000") + Decimal("7500") + Decimal("5000")
        )
        assert breakdown.total_monthly_deductions == expected


# ---------------------------------------------------------------------------
# Dependency Injection Pattern Tests
# ---------------------------------------------------------------------------


class TestDependencyInjectionPattern:
    """Tests demonstrating DI pattern with tax calculators."""

    def test_service_can_accept_any_calculator(
        self, org_id, gross_monthly, basic_monthly
    ):
        """Demonstrate that any TaxCalculator can be injected."""

        # Simulate a service that accepts a TaxCalculator
        class MockPayrollService:
            def __init__(self, tax_calculator: TaxCalculator):
                self.tax_calculator = tax_calculator

            def calculate_deductions(
                self, org_id, gross: Decimal, basic: Decimal
            ) -> TaxResult:
                return self.tax_calculator.calculate(org_id, gross, basic)

        # Use different calculators
        calculators = [
            ZeroTaxCalculator(),
            FixedTaxCalculator(monthly_tax=Decimal("10000")),
            PercentageTaxCalculator(tax_rate=Decimal("0.15")),
        ]

        for calculator in calculators:
            service = MockPayrollService(tax_calculator=calculator)
            result = service.calculate_deductions(org_id, gross_monthly, basic_monthly)

            # All results should be TaxResult compatible
            assert hasattr(result, "monthly_tax")
            assert hasattr(result, "monthly_pension")
            assert hasattr(result, "total_monthly_deductions")

    def test_can_swap_calculator_for_testing(
        self, org_id, gross_monthly, basic_monthly
    ):
        """Demonstrate swapping real calculator with mock for testing."""

        # In production: use real PAYE calculator
        # mock_db = get_real_db()
        # calculator = PAYECalculator(mock_db)

        # In tests: use fixed calculator for predictable results
        calculator = FixedTaxCalculator(
            monthly_tax=Decimal("50000"),
            monthly_pension=Decimal("24000"),
        )

        result = calculator.calculate(org_id, gross_monthly, basic_monthly)

        # Predictable assertions possible
        assert result.monthly_tax == Decimal("50000")
        assert result.monthly_pension == Decimal("24000")
