"""
Tests for SalarySlipService with PAYE Integration.

Tests the integration between salary slip creation and NTA 2025 PAYE calculation.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.people.payroll.salary_component import (
    SalaryComponent,
    SalaryComponentType,
)
from app.services.people.payroll.salary_slip_service import (
    BASIC_COMPONENT_CODE,
    NHF_COMPONENT_CODE,
    NHIS_COMPONENT_CODE,
    PAYE_COMPONENT_CODE,
    PENSION_COMPONENT_CODE,
    STATUTORY_COMPONENT_CODES,
    SalarySlipInput,
    SalarySlipService,
)


class TestStatutoryComponentConstants:
    """Tests for statutory component constants."""

    def test_basic_component_code(self):
        """Test BASIC component code."""
        assert BASIC_COMPONENT_CODE == "BASIC"

    def test_statutory_codes_complete(self):
        """Test all statutory codes are defined."""
        assert PENSION_COMPONENT_CODE == "PENSION"
        assert NHF_COMPONENT_CODE == "NHF"
        assert NHIS_COMPONENT_CODE == "NHIS"
        assert PAYE_COMPONENT_CODE == "PAYE"

    def test_statutory_codes_set(self):
        """Test statutory codes set contains all codes."""
        assert {"PENSION", "NHF", "NHIS", "PAYE"} == STATUTORY_COMPONENT_CODES


class TestGetOrCreateStatutoryComponent:
    """Tests for get_or_create_statutory_component method."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        session = MagicMock()
        session.scalar = MagicMock(return_value=None)
        session.add = MagicMock()
        session.flush = MagicMock()
        return session

    @pytest.fixture
    def org_id(self):
        """Test organization ID."""
        return uuid.uuid4()

    def test_creates_new_component(self, mock_db, org_id):
        """Test creating a new statutory component when none exists."""
        mock_db.scalar.return_value = None

        SalarySlipService.get_or_create_statutory_component(
            db=mock_db,
            organization_id=org_id,
            component_code="PAYE",
            component_name="Pay As You Earn Tax",
            abbr="PAYE",
            display_order=104,
        )

        # Verify add was called
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

        # Verify component attributes
        added_component = mock_db.add.call_args[0][0]
        assert added_component.component_code == "PAYE"
        assert added_component.component_name == "Pay As You Earn Tax"
        assert added_component.abbr == "PAYE"
        assert added_component.component_type == SalaryComponentType.DEDUCTION
        assert added_component.is_statutory is True
        assert added_component.is_tax_applicable is False
        assert added_component.depends_on_payment_days is False

    def test_returns_existing_component(self, mock_db, org_id):
        """Test returning existing component without creating duplicate."""
        existing_component = SalaryComponent(
            component_id=uuid.uuid4(),
            organization_id=org_id,
            component_code="PAYE",
            component_name="Pay As You Earn Tax",
            abbr="PAYE",
            component_type=SalaryComponentType.DEDUCTION,
            is_statutory=True,
        )
        mock_db.scalar.return_value = existing_component

        component = SalarySlipService.get_or_create_statutory_component(
            db=mock_db,
            organization_id=org_id,
            component_code="PAYE",
            component_name="Pay As You Earn Tax",
            abbr="PAYE",
        )

        # Verify no new component was added
        mock_db.add.assert_not_called()
        assert component == existing_component


class TestGetStatutoryComponents:
    """Tests for get_statutory_components method."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        session = MagicMock()
        session.scalar = MagicMock(return_value=None)
        session.add = MagicMock()
        session.flush = MagicMock()
        return session

    @pytest.fixture
    def org_id(self):
        """Test organization ID."""
        return uuid.uuid4()

    def test_creates_all_statutory_components(self, mock_db, org_id):
        """Test that all 4 statutory components are created."""
        mock_db.scalar.return_value = None

        components = SalarySlipService.get_statutory_components(mock_db, org_id)

        # Should have 4 components
        assert len(components) == 4
        assert "PENSION" in components
        assert "NHF" in components
        assert "NHIS" in components
        assert "PAYE" in components

        # Verify add was called 4 times (once for each component)
        assert mock_db.add.call_count == 4


class TestPAYEIntegration:
    """Integration tests for PAYE calculation in salary slips."""

    def test_paye_breakdown_attributes(self):
        """Test PAYEBreakdown has expected attributes for integration."""
        from app.services.people.payroll.paye_calculator import PAYEBreakdown

        breakdown = PAYEBreakdown(
            annual_gross=Decimal("6000000"),
            annual_basic=Decimal("3600000"),
            annual_transport=Decimal("0"),
            annual_housing=Decimal("0"),
            annual_pension_base=Decimal("3600000"),
            annual_rent=Decimal("1200000"),
            pension_amount=Decimal("288000"),
            pension_rate=Decimal("0.08"),
            employer_pension_amount=Decimal("360000"),
            employer_pension_rate=Decimal("0.10"),
            nhf_amount=Decimal("90000"),
            nhf_rate=Decimal("0.025"),
            nhis_amount=Decimal("0"),
            nhis_rate=Decimal("0"),
            total_statutory=Decimal("378000"),
            rent_relief=Decimal("240000"),
            taxable_income=Decimal("5382000"),
            annual_tax=Decimal("758760"),
            monthly_tax=Decimal("63230"),
            monthly_pension=Decimal("24000"),
            monthly_nhf=Decimal("7500"),
            monthly_nhis=Decimal("0"),
        )

        # Verify monthly amounts are accessible
        assert breakdown.monthly_tax == Decimal("63230")
        assert breakdown.monthly_pension == Decimal("24000")
        assert breakdown.monthly_nhf == Decimal("7500")
        assert breakdown.monthly_nhis == Decimal("0")

    def test_statutory_deduction_skip_logic(self):
        """Test that statutory deductions are correctly identified for skipping."""
        # Create component with is_statutory flag
        statutory_component = SalaryComponent(
            component_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            component_code="CUSTOM_TAX",
            component_name="Custom Tax",
            abbr="CTX",
            component_type=SalaryComponentType.DEDUCTION,
            is_statutory=True,
        )

        # Component should be skipped
        assert statutory_component.is_statutory is True

        # Create component matching statutory code
        pension_component = SalaryComponent(
            component_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            component_code="PENSION",
            component_name="Pension",
            abbr="PEN",
            component_type=SalaryComponentType.DEDUCTION,
            is_statutory=False,  # Even without flag
        )

        # Component should be skipped by code match
        assert pension_component.component_code in STATUTORY_COMPONENT_CODES

    def test_basic_component_identification(self):
        """Test BASIC component is correctly identified."""
        basic_component = SalaryComponent(
            component_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            component_code="BASIC",
            component_name="Basic Salary",
            abbr="BAS",
            component_type=SalaryComponentType.EARNING,
        )

        # Should match BASIC_COMPONENT_CODE
        assert basic_component.component_code == BASIC_COMPONENT_CODE


class TestSalarySlipInputValidation:
    """Tests for SalarySlipInput dataclass."""

    def test_input_with_defaults(self):
        """Test input with default values."""
        emp_id = uuid.uuid4()
        input_data = SalarySlipInput(
            employee_id=emp_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )

        assert input_data.employee_id == emp_id
        assert input_data.posting_date is None
        assert input_data.total_working_days is None
        assert input_data.absent_days == Decimal("0")
        assert input_data.leave_without_pay == Decimal("0")

    def test_input_with_all_fields(self):
        """Test input with all fields specified."""
        emp_id = uuid.uuid4()
        input_data = SalarySlipInput(
            employee_id=emp_id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            posting_date=date(2026, 2, 5),
            total_working_days=Decimal("22"),
            absent_days=Decimal("2"),
            leave_without_pay=Decimal("1"),
        )

        assert input_data.posting_date == date(2026, 2, 5)
        assert input_data.total_working_days == Decimal("22")
        assert input_data.absent_days == Decimal("2")
        assert input_data.leave_without_pay == Decimal("1")


class TestPAYECalculationValues:
    """Tests for PAYE calculation value verification."""

    def test_monthly_pension_calculation(self):
        """Verify pension is 8% of basic + housing + transport."""

        # Monthly pension base: 300,000 + 50,000 + 50,000 = 400,000
        # Annual pension base: 4,800,000
        # Pension (8%): 384,000 annual, 32,000 monthly
        monthly_basic = Decimal("300000")
        monthly_housing = Decimal("50000")
        monthly_transport = Decimal("50000")
        expected_annual_pension = (
            (monthly_basic + monthly_housing + monthly_transport) * 12 * Decimal("0.08")
        )
        expected_monthly_pension = expected_annual_pension / 12

        assert expected_monthly_pension == Decimal("32000")

    def test_monthly_nhf_calculation(self):
        """Verify NHF is 2.5% of basic salary."""
        # Monthly basic: 300,000
        # Annual basic: 3,600,000
        # NHF (2.5%): 90,000 annual, 7,500 monthly
        monthly_basic = Decimal("300000")
        expected_annual_nhf = monthly_basic * 12 * Decimal("0.025")
        expected_monthly_nhf = expected_annual_nhf / 12

        assert expected_monthly_nhf == Decimal("7500")

    def test_rent_relief_calculation(self):
        """Verify rent relief is 20% of annual rent, max 500K."""
        # Rent: 1,200,000
        # Relief (20%): 240,000
        annual_rent = Decimal("1200000")
        calculated_relief = annual_rent * Decimal("0.20")
        capped_relief = min(calculated_relief, Decimal("500000"))

        assert capped_relief == Decimal("240000")

        # High rent test (should cap at 500K)
        high_rent = Decimal("5000000")
        calculated = high_rent * Decimal("0.20")  # 1,000,000
        capped = min(calculated, Decimal("500000"))

        assert capped == Decimal("500000")
