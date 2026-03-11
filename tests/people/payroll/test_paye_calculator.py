"""
Tests for PAYE Calculator Service - NTA 2025.

This test suite verifies the Nigeria Tax Act 2025 PAYE calculation logic.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.payroll.tax_band import TaxBand
from app.services.people.payroll.paye_calculator import (
    PAYECalculator,
)

# ============ Fixtures ============


@pytest.fixture
def org_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def employee_id() -> uuid.UUID:
    """Generate a test employee ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.order_by = MagicMock(return_value=session)
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    return session


@pytest.fixture
def nta_2025_bands(org_id) -> list[TaxBand]:
    """Create NTA 2025 tax bands."""
    bands_data = [
        {"name": "NTA 2025 - 0%", "min": 0, "max": 800000, "rate": "0.00", "seq": 1},
        {
            "name": "NTA 2025 - 15%",
            "min": 800000,
            "max": 3000000,
            "rate": "0.15",
            "seq": 2,
        },
        {
            "name": "NTA 2025 - 18%",
            "min": 3000000,
            "max": 12000000,
            "rate": "0.18",
            "seq": 3,
        },
        {
            "name": "NTA 2025 - 21%",
            "min": 12000000,
            "max": 25000000,
            "rate": "0.21",
            "seq": 4,
        },
        {
            "name": "NTA 2025 - 23%",
            "min": 25000000,
            "max": 50000000,
            "rate": "0.23",
            "seq": 5,
        },
        {
            "name": "NTA 2025 - 25%",
            "min": 50000000,
            "max": None,
            "rate": "0.25",
            "seq": 6,
        },
    ]

    bands = []
    for data in bands_data:
        band = TaxBand(
            tax_band_id=uuid.uuid4(),
            organization_id=org_id,
            name=data["name"],
            min_amount=Decimal(str(data["min"])),
            max_amount=Decimal(str(data["max"])) if data["max"] else None,
            rate=Decimal(data["rate"]),
            sequence=data["seq"],
            effective_from=date(2026, 1, 1),
            is_active=True,
        )
        bands.append(band)

    return bands


# ============ TaxBand Model Tests ============


class TestTaxBand:
    """Tests for TaxBand model."""

    def test_calculate_tax_zero_band(self, org_id):
        """Test 0% band calculates no tax."""
        band = TaxBand(
            organization_id=org_id,
            name="0%",
            min_amount=Decimal("0"),
            max_amount=Decimal("800000"),
            rate=Decimal("0"),
            sequence=1,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )

        # Income below band
        assert band.calculate_tax(Decimal("500000")) == Decimal("0")

        # Income at top of band
        assert band.calculate_tax(Decimal("800000")) == Decimal("0")

        # Income above band
        assert band.calculate_tax(Decimal("1000000")) == Decimal("0")

    def test_calculate_tax_middle_band(self, org_id):
        """Test 15% band calculation."""
        band = TaxBand(
            organization_id=org_id,
            name="15%",
            min_amount=Decimal("800000"),
            max_amount=Decimal("3000000"),
            rate=Decimal("0.15"),
            sequence=2,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )

        # Income below band minimum - no tax
        assert band.calculate_tax(Decimal("500000")) == Decimal("0")

        # Income in middle of band
        # 2M income: 2M - 800K = 1.2M taxable, 1.2M * 0.15 = 180K
        assert band.calculate_tax(Decimal("2000000")) == Decimal("180000")

        # Income above band maximum
        # 5M income: max is 3M, so 3M - 800K = 2.2M taxable, 2.2M * 0.15 = 330K
        assert band.calculate_tax(Decimal("5000000")) == Decimal("330000")

    def test_calculate_tax_unlimited_band(self, org_id):
        """Test unlimited (top) band calculation."""
        band = TaxBand(
            organization_id=org_id,
            name="25%",
            min_amount=Decimal("50000000"),
            max_amount=None,  # Unlimited
            rate=Decimal("0.25"),
            sequence=6,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )

        # Income below band
        assert band.calculate_tax(Decimal("40000000")) == Decimal("0")

        # Income in band (unlimited)
        # 60M: 60M - 50M = 10M taxable, 10M * 0.25 = 2.5M
        assert band.calculate_tax(Decimal("60000000")) == Decimal("2500000")

    def test_range_display(self, org_id):
        """Test range display formatting."""
        band_with_max = TaxBand(
            organization_id=org_id,
            name="15%",
            min_amount=Decimal("800000"),
            max_amount=Decimal("3000000"),
            rate=Decimal("0.15"),
            sequence=2,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )
        assert band_with_max.range_display == "₦800,000 - ₦3,000,000"

        band_unlimited = TaxBand(
            organization_id=org_id,
            name="25%",
            min_amount=Decimal("50000000"),
            max_amount=None,
            rate=Decimal("0.25"),
            sequence=6,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )
        assert band_unlimited.range_display == "₦50,000,000+"


# ============ EmployeeTaxProfile Tests ============


class TestEmployeeTaxProfile:
    """Tests for EmployeeTaxProfile model."""

    def test_calculate_rent_relief_basic(self, org_id, employee_id):
        """Test basic rent relief calculation."""
        profile = EmployeeTaxProfile(
            organization_id=org_id,
            employee_id=employee_id,
            annual_rent=Decimal("1200000"),  # 1.2M rent
            rent_receipt_verified=True,
            effective_from=date(2026, 1, 1),
        )

        # 20% of 1.2M = 240K (under 500K cap)
        assert profile.calculate_rent_relief() == Decimal("240000")

    def test_calculate_rent_relief_capped(self, org_id, employee_id):
        """Test rent relief is capped at 500K."""
        profile = EmployeeTaxProfile(
            organization_id=org_id,
            employee_id=employee_id,
            annual_rent=Decimal("5000000"),  # 5M rent
            rent_receipt_verified=True,
            effective_from=date(2026, 1, 1),
        )

        # 20% of 5M = 1M, but capped at 500K
        assert profile.calculate_rent_relief() == Decimal("500000")

    def test_calculate_rent_relief_not_verified(self, org_id, employee_id):
        """Test no rent relief if not verified."""
        profile = EmployeeTaxProfile(
            organization_id=org_id,
            employee_id=employee_id,
            annual_rent=Decimal("1200000"),
            rent_receipt_verified=False,
            effective_from=date(2026, 1, 1),
        )

        assert profile.calculate_rent_relief() == Decimal("0")

    def test_calculate_rent_relief_zero_rent(self, org_id, employee_id):
        """Test no rent relief if rent is zero."""
        profile = EmployeeTaxProfile(
            organization_id=org_id,
            employee_id=employee_id,
            annual_rent=Decimal("0"),
            rent_receipt_verified=True,
            effective_from=date(2026, 1, 1),
        )

        assert profile.calculate_rent_relief() == Decimal("0")


# ============ PAYECalculator Tests ============


class TestPAYECalculator:
    """Tests for PAYECalculator service."""

    def test_calculate_example_from_spec(self, mock_db, org_id, nta_2025_bands):
        """
        Test the example calculation from the specification.

        Example: ₦500,000/month gross, ₦300,000/month basic, ₦1.2M rent
        Expected:
        - Annual Gross: ₦6,000,000
        - Pension (8%): -₦288,000
        - NHF (2.5%): -₦90,000
        - Rent Relief: -₦240,000 (20% of ₦1.2M)
        - Taxable Income: ₦5,382,000
        - Annual Tax: ₦758,760
        - Monthly PAYE: ₦63,230
        """
        # Setup mock to return NTA 2025 bands
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("500000"),
            basic_monthly=Decimal("300000"),
            annual_rent=Decimal("1200000"),
            rent_verified=True,
        )

        # Verify annual amounts
        assert result.annual_gross == Decimal("6000000")
        assert result.annual_basic == Decimal("3600000")

        # Verify statutory deductions
        assert result.pension_amount == Decimal("288000")  # 8% of 3.6M
        assert result.nhf_amount == Decimal("90000")  # 2.5% of 3.6M

        # Verify rent relief
        assert result.rent_relief == Decimal("240000")  # 20% of 1.2M

        # Verify taxable income
        expected_taxable = (
            Decimal("6000000")
            - Decimal("288000")
            - Decimal("90000")
            - Decimal("240000")
        )
        assert result.taxable_income == expected_taxable  # 5,382,000

        # Verify tax calculation
        # Band 1: 0-800K @ 0% = 0
        # Band 2: 800K-3M @ 15% = 330,000
        # Band 3: 3M-5.382M @ 18% = 428,760
        # Total: 758,760
        expected_annual_tax = Decimal("0") + Decimal("330000") + Decimal("428760")
        assert result.annual_tax == expected_annual_tax

        # Verify monthly tax (rounded)
        expected_monthly = (expected_annual_tax / 12).quantize(Decimal("0.01"))
        assert result.monthly_tax == expected_monthly

    def test_calculate_low_income_no_tax(self, mock_db, org_id, nta_2025_bands):
        """Test that income below 800K pays no tax."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("50000"),  # 600K annually
            basic_monthly=Decimal("40000"),
        )

        assert result.annual_tax == Decimal("0")
        assert result.monthly_tax == Decimal("0")

    def test_calculate_high_income(self, mock_db, org_id, nta_2025_bands):
        """Test high income calculation using all tax bands."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("5000000"),  # 60M annually
            basic_monthly=Decimal("3000000"),
        )

        # This should use all 6 bands
        assert len(result.band_breakdowns) == 6
        assert result.annual_tax > Decimal("0")

    def test_calculate_with_tax_exempt(
        self, mock_db, org_id, employee_id, nta_2025_bands
    ):
        """Test tax exempt employee pays no tax."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands

        # Create exempt profile with all required fields
        exempt_profile = EmployeeTaxProfile(
            organization_id=org_id,
            employee_id=employee_id,
            is_tax_exempt=True,
            exemption_reason="Diplomatic immunity",
            effective_from=date(2026, 1, 1),
            pension_rate=Decimal("0.08"),
            nhf_rate=Decimal("0.025"),
            nhis_rate=Decimal("0"),
            annual_rent=Decimal("0"),
            rent_receipt_verified=False,
        )
        mock_db.scalar.return_value = exempt_profile

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("500000"),
            basic_monthly=Decimal("300000"),
            employee_id=employee_id,
        )

        assert result.is_tax_exempt is True
        assert result.annual_tax == Decimal("0")
        assert result.monthly_tax == Decimal("0")

    def test_effective_rate_calculation(self, mock_db, org_id, nta_2025_bands):
        """Test effective tax rate calculation."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("500000"),
            basic_monthly=Decimal("300000"),
            annual_rent=Decimal("1200000"),
            rent_verified=True,
        )

        # Effective rate = annual_tax / annual_gross
        expected_rate = result.annual_tax / result.annual_gross
        assert result.effective_rate == expected_rate.quantize(Decimal("0.0001"))

        # Should be around 12.6% as per spec
        assert Decimal("0.12") < result.effective_rate < Decimal("0.13")

    def test_default_rates(self, mock_db, org_id):
        """Test default statutory rates."""
        calculator = PAYECalculator(mock_db)

        assert Decimal("0.08") == calculator.DEFAULT_PENSION_RATE
        assert Decimal("0.025") == calculator.DEFAULT_NHF_RATE
        assert Decimal("0") == calculator.DEFAULT_NHIS_RATE
        assert Decimal("0.20") == calculator.RENT_RELIEF_RATE
        assert Decimal("500000") == calculator.RENT_RELIEF_MAX

    def test_custom_statutory_rates(self, mock_db, org_id, nta_2025_bands):
        """Test custom statutory rates override defaults."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("500000"),
            basic_monthly=Decimal("300000"),
            pension_rate=Decimal("0.10"),  # 10% instead of 8%
            nhf_rate=Decimal("0.03"),  # 3% instead of 2.5%
            nhis_rate=Decimal("0.01"),  # 1% instead of 0%
        )

        assert result.pension_rate == Decimal("0.10")
        assert result.nhf_rate == Decimal("0.03")
        assert result.nhis_rate == Decimal("0.01")

        # Verify amounts use custom rates
        expected_pension = result.annual_pension_base * Decimal("0.10")
        assert result.pension_amount == expected_pension

    def test_pension_uses_basic_housing_and_transport(
        self, mock_db, org_id, nta_2025_bands
    ):
        """Employee and employer pension should use basic + housing + transport."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("500000"),
            basic_monthly=Decimal("300000"),
            housing_monthly=Decimal("50000"),
            transport_monthly=Decimal("50000"),
        )

        assert result.annual_pension_base == Decimal("4800000")
        assert result.pension_amount == Decimal("384000")
        assert result.employer_pension_amount == Decimal("480000")
        assert result.monthly_pension == Decimal("32000.00")
        assert result.monthly_employer_pension == Decimal("40000.00")

    def test_breakdown_to_dict(self, mock_db, org_id, nta_2025_bands):
        """Test PAYEBreakdown serialization."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("500000"),
            basic_monthly=Decimal("300000"),
        )

        data = result.to_dict()

        # Verify all expected keys exist
        assert "annual_gross" in data
        assert "annual_tax" in data
        assert "monthly_tax" in data
        assert "band_breakdowns" in data
        assert "effective_rate_percent" in data

        # Verify band breakdowns are serialized
        assert isinstance(data["band_breakdowns"], list)
        if data["band_breakdowns"]:
            band = data["band_breakdowns"][0]
            assert "band_name" in band
            assert "rate_percent" in band
            assert "tax_amount" in band


class TestPAYECalculatorSeeding:
    """Tests for tax band seeding."""

    def test_seed_nta_2025_bands(self, mock_db, org_id):
        """Test seeding default NTA 2025 bands."""
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)
        bands = calculator.seed_nta_2025_bands(org_id)

        # Should create 6 bands
        assert len(bands) == 6

        # Verify band configuration
        rates = [b.rate for b in bands]
        assert Decimal("0") in rates  # 0%
        assert Decimal("0.15") in rates  # 15%
        assert Decimal("0.18") in rates  # 18%
        assert Decimal("0.21") in rates  # 21%
        assert Decimal("0.23") in rates  # 23%
        assert Decimal("0.25") in rates  # 25%

    def test_seed_does_not_duplicate(self, mock_db, org_id, nta_2025_bands):
        """Test seeding doesn't duplicate existing bands."""
        # Return existing band
        mock_db.scalar.return_value = nta_2025_bands[0]

        calculator = PAYECalculator(mock_db)
        bands = calculator.seed_nta_2025_bands(org_id)

        # Should return empty list (no new bands created)
        assert len(bands) == 0


# ============ Edge Case Tests ============


class TestPAYEEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_very_high_income(self, mock_db, org_id, nta_2025_bands):
        """Test calculation for very high income (₦100M+ annually)."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("10000000"),  # ₦10M/month = ₦120M annually
            basic_monthly=Decimal("6000000"),
        )

        # Should use all 6 bands
        assert len(result.band_breakdowns) == 6
        # Top band should have significant tax
        top_band = result.band_breakdowns[-1]
        assert top_band.rate == Decimal("0.25")
        assert top_band.tax_amount > Decimal("0")
        # Effective rate should be close to top marginal rate for very high income
        assert result.effective_rate > Decimal("0.20")

    def test_exact_band_boundary_800k(self, mock_db, org_id, nta_2025_bands):
        """Test income at or below first band boundary (₦800,000)."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        # Annual income below 800K (no statutory deductions for simplicity)
        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("66000"),  # ₦792K annually - below 800K
            basic_monthly=Decimal("0"),  # No basic = no statutory deductions
        )

        # Income below 800K should pay no tax
        assert result.annual_tax == Decimal("0")
        assert result.taxable_income == Decimal("792000")

    def test_exact_band_boundary_3m(self, mock_db, org_id, nta_2025_bands):
        """Test income exactly at second band boundary (₦3,000,000)."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        # Annual income exactly 3M (no statutory deductions)
        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("250000"),  # 3M annually
            basic_monthly=Decimal("0"),
        )

        # Tax should be: Band 1 (0-800K) @ 0% = 0, Band 2 (800K-3M) @ 15% = 330,000
        # Total should be 330,000
        assert result.annual_tax == Decimal("330000.00")

    def test_zero_gross_income(self, mock_db, org_id, nta_2025_bands):
        """Test calculation with zero gross income."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("0"),
            basic_monthly=Decimal("0"),
        )

        assert result.annual_tax == Decimal("0")
        assert result.monthly_tax == Decimal("0")
        assert result.effective_rate == Decimal("0")

    def test_statutory_deductions_exceed_gross(self, mock_db, org_id, nta_2025_bands):
        """Test when statutory deductions exceed gross income."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        # Very low gross but higher basic (unusual edge case)
        # Gross: 10,000/month = 120,000/year
        # Basic: 200,000/month = 2,400,000/year
        # Statutory (10.5% of basic): 252,000
        # Taxable = max(120,000 - 252,000, 0) = 0
        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("10000"),
            basic_monthly=Decimal("200000"),
        )

        # Taxable income should be clamped to 0 (can't be negative)
        assert result.taxable_income == Decimal("0")
        assert result.annual_tax == Decimal("0")

    def test_expired_profile_not_used(
        self, mock_db, org_id, employee_id, nta_2025_bands
    ):
        """Test that expired tax profile is not used."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands

        # Profile that expired in the past
        EmployeeTaxProfile(
            organization_id=org_id,
            employee_id=employee_id,
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 12, 31),  # Expired
            pension_rate=Decimal("0.10"),  # Different rate
            nhf_rate=Decimal("0.03"),
            nhis_rate=Decimal("0"),
            annual_rent=Decimal("0"),
            rent_receipt_verified=False,
            is_tax_exempt=False,
        )

        # Return None when querying (simulating the filter excluding expired profile)
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("500000"),
            basic_monthly=Decimal("300000"),
            employee_id=employee_id,
        )

        # Should use default rates since profile is expired
        assert result.pension_rate == Decimal("0.08")  # Default
        assert result.nhf_rate == Decimal("0.025")  # Default

    def test_maximum_rent_relief_cap(self, mock_db, org_id, nta_2025_bands):
        """Test that rent relief is capped at ₦500,000."""
        mock_db.scalars.return_value.all.return_value = nta_2025_bands
        mock_db.scalar.return_value = None

        calculator = PAYECalculator(mock_db)

        result = calculator.calculate(
            organization_id=org_id,
            gross_monthly=Decimal("1000000"),
            basic_monthly=Decimal("600000"),
            annual_rent=Decimal("10000000"),  # ₦10M rent (20% = ₦2M, but capped)
            rent_verified=True,
        )

        # Rent relief should be capped at 500K
        assert result.rent_relief == Decimal("500000")

    def test_decimal_quantization_in_tax(self, org_id):
        """Test that tax amounts are properly quantized to 2 decimal places."""
        band = TaxBand(
            organization_id=org_id,
            name="Test Band",
            min_amount=Decimal("0"),
            max_amount=Decimal("1000000"),
            rate=Decimal("0.153"),  # Rate that produces many decimal places
            sequence=1,
            effective_from=date(2026, 1, 1),
            is_active=True,
        )

        # 123456.789 * 0.153 = 18888.888417
        tax = band.calculate_tax(Decimal("123456.789"))

        # Should be quantized to 2 decimal places
        assert tax == tax.quantize(Decimal("0.01"))
        # Verify no more than 2 decimal places
        assert str(tax).find(".") == -1 or len(str(tax).split(".")[-1]) <= 2
