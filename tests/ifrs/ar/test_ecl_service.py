"""
Unit tests for ECLService - IFRS 9 Expected Credit Loss Calculation.

Tests cover:
- Simplified approach (provision matrix)
- General approach (PD/LGD/EAD model)
- ECL stage determination
- Provision movement calculations
- Summary and listing methods
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock
from uuid import uuid4

from fastapi import HTTPException

from app.services.finance.ar.ecl import (
    ECLService,
    ECLCalculationInput,
    GeneralApproachInput,
    DEFAULT_AGING_BUCKETS,
)


# Mock enums
class MockECLMethodology:
    SIMPLIFIED = "SIMPLIFIED"
    GENERAL = "GENERAL"


class MockECLStageValue:
    """Mock ECL stage value with .value property."""

    def __init__(self, stage_value):
        self._value = stage_value

    @property
    def value(self):
        return self._value


class MockECLStage:
    STAGE_1 = MockECLStageValue("STAGE_1")
    STAGE_2 = MockECLStageValue("STAGE_2")
    STAGE_3 = MockECLStageValue("STAGE_3")


class MockInvoiceStatus:
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    VOID = "VOID"


class MockCustomer:
    """Mock Customer model."""

    def __init__(
        self,
        customer_id=None,
        organization_id=None,
        name="Test Customer",
    ):
        self.customer_id = customer_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.name = name


class MockInvoice:
    """Mock Invoice model."""

    def __init__(
        self,
        invoice_id=None,
        organization_id=None,
        customer_id=None,
        invoice_number="INV-000001",
        due_date=None,
        total_amount=Decimal("1000.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("1000.00"),
        status=None,
    ):
        self.invoice_id = invoice_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.customer_id = customer_id or uuid4()
        self.invoice_number = invoice_number
        self.due_date = due_date or date.today()
        self.total_amount = total_amount
        self.amount_paid = amount_paid
        self.balance_due = balance_due
        self.status = status or MockInvoiceStatus.POSTED


class MockExpectedCreditLoss:
    """Mock ExpectedCreditLoss model."""

    def __init__(
        self,
        ecl_id=None,
        organization_id=None,
        calculation_date=None,
        fiscal_period_id=None,
        methodology=None,
        customer_id=None,
        portfolio_segment=None,
        aging_bucket=None,
        gross_carrying_amount=Decimal("10000.00"),
        historical_loss_rate=Decimal("0.01"),
        forward_looking_adjustment=Decimal("0"),
        ecl_stage=None,
        provision_amount=Decimal("100.00"),
        provision_movement=Decimal("0"),
        probability_of_default=None,
        loss_given_default=None,
        exposure_at_default=None,
        ecl_12_month=None,
        ecl_lifetime=None,
        credit_risk_rating=None,
        significant_increase_indicator=False,
        calculation_details=None,
    ):
        self.ecl_id = ecl_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.calculation_date = calculation_date or date.today()
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.methodology = methodology or MockECLMethodology.SIMPLIFIED
        self.customer_id = customer_id
        self.portfolio_segment = portfolio_segment
        self.aging_bucket = aging_bucket
        self.gross_carrying_amount = gross_carrying_amount
        self.historical_loss_rate = historical_loss_rate
        self.forward_looking_adjustment = forward_looking_adjustment
        self.ecl_stage = ecl_stage or MockECLStage()
        self.provision_amount = provision_amount
        self.provision_movement = provision_movement
        self.probability_of_default = probability_of_default
        self.loss_given_default = loss_given_default
        self.exposure_at_default = exposure_at_default
        self.ecl_12_month = ecl_12_month
        self.ecl_lifetime = ecl_lifetime
        self.credit_risk_rating = credit_risk_rating
        self.significant_increase_indicator = significant_increase_indicator
        self.calculation_details = calculation_details


# ===================== CALCULATE SIMPLIFIED TESTS =====================

class TestCalculateSimplified:
    """Tests for simplified ECL calculation."""

    @patch("app.services.ifrs.ar.ecl.InvoiceStatus")
    @patch("app.services.ifrs.ar.ecl.Invoice")
    def test_calculate_simplified_current_invoices(self, mock_invoice_class, mock_status_class):
        """Test simplified calculation with current invoices."""
        db = MagicMock()
        org_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        # Configure mock Invoice class to support comparison operators
        # This is needed because Invoice.balance_due > 0 is used in filters
        mock_invoice_class.balance_due = MagicMock()
        mock_invoice_class.balance_due.__gt__ = MagicMock(return_value=MagicMock())
        mock_invoice_class.organization_id = MagicMock()
        mock_invoice_class.status = MagicMock()
        mock_invoice_class.customer_id = MagicMock()

        # Create current invoices (not past due)
        calculation_date = date.today()
        invoices = [
            MockInvoice(
                due_date=calculation_date,  # Due today = current
                balance_due=Decimal("5000.00"),
            ),
            MockInvoice(
                due_date=calculation_date + timedelta(days=10),  # Not yet due
                balance_due=Decimal("3000.00"),
            ),
        ]

        # Setup mock query chain
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = invoices
        db.query.return_value = mock_query

        input_data = ECLCalculationInput(
            calculation_date=calculation_date,
            fiscal_period_id=fiscal_period_id,
        )

        with patch("app.services.ifrs.ar.ecl.ECLService._get_prior_provision") as mock_prior:
            mock_prior.return_value = Decimal("0")

            result = ECLService.calculate_simplified(db, org_id, input_data, user_id)

        assert result is not None
        assert result.calculation_date == calculation_date
        assert result.total_gross_carrying_amount == Decimal("8000.00")
        db.commit.assert_called_once()

    @patch("app.services.ifrs.ar.ecl.InvoiceStatus")
    @patch("app.services.ifrs.ar.ecl.Invoice")
    def test_calculate_simplified_overdue_invoices(self, mock_invoice_class, mock_status_class):
        """Test simplified calculation with overdue invoices in various buckets."""
        db = MagicMock()
        org_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        # Configure mock Invoice class to support comparison operators
        mock_invoice_class.balance_due = MagicMock()
        mock_invoice_class.balance_due.__gt__ = MagicMock(return_value=MagicMock())
        mock_invoice_class.organization_id = MagicMock()
        mock_invoice_class.status = MagicMock()
        mock_invoice_class.customer_id = MagicMock()

        calculation_date = date.today()
        invoices = [
            MockInvoice(
                due_date=calculation_date - timedelta(days=15),  # 1-30 days
                balance_due=Decimal("2000.00"),
            ),
            MockInvoice(
                due_date=calculation_date - timedelta(days=45),  # 31-60 days
                balance_due=Decimal("1500.00"),
            ),
            MockInvoice(
                due_date=calculation_date - timedelta(days=100),  # 91-120 days
                balance_due=Decimal("1000.00"),
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = invoices
        db.query.return_value = mock_query

        input_data = ECLCalculationInput(
            calculation_date=calculation_date,
            fiscal_period_id=fiscal_period_id,
        )

        with patch("app.services.ifrs.ar.ecl.ECLService._get_prior_provision") as mock_prior:
            mock_prior.return_value = Decimal("50.00")

            result = ECLService.calculate_simplified(db, org_id, input_data, user_id)

        assert result is not None
        # Provision movement = new - prior
        assert result.provision_movement == result.total_provision - Decimal("50.00")

    @patch("app.services.ifrs.ar.ecl.InvoiceStatus")
    @patch("app.services.ifrs.ar.ecl.Invoice")
    def test_calculate_simplified_with_custom_loss_rates(self, mock_invoice_class, mock_status_class):
        """Test simplified calculation with custom loss rates."""
        db = MagicMock()
        org_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        # Configure mock Invoice class to support comparison operators
        mock_invoice_class.balance_due = MagicMock()
        mock_invoice_class.balance_due.__gt__ = MagicMock(return_value=MagicMock())
        mock_invoice_class.organization_id = MagicMock()
        mock_invoice_class.status = MagicMock()
        mock_invoice_class.customer_id = MagicMock()

        calculation_date = date.today()
        invoices = [
            MockInvoice(
                due_date=calculation_date,
                balance_due=Decimal("10000.00"),
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = invoices
        db.query.return_value = mock_query

        # Custom loss rates
        custom_rates = {
            "CURRENT": Decimal("0.02"),  # Higher than default
            "1_30_DAYS": Decimal("0.05"),
            "31_60_DAYS": Decimal("0.10"),
            "61_90_DAYS": Decimal("0.15"),
            "91_120_DAYS": Decimal("0.25"),
            "OVER_120_DAYS": Decimal("0.50"),
        }

        input_data = ECLCalculationInput(
            calculation_date=calculation_date,
            fiscal_period_id=fiscal_period_id,
            custom_loss_rates=custom_rates,
        )

        with patch("app.services.ifrs.ar.ecl.ECLService._get_prior_provision") as mock_prior:
            mock_prior.return_value = Decimal("0")

            result = ECLService.calculate_simplified(db, org_id, input_data, user_id)

        assert result is not None
        # Verify custom rates were used (through buckets)
        current_bucket = next(b for b in result.buckets if b.bucket_name == "CURRENT")
        assert current_bucket.loss_rate == Decimal("0.02")

    @patch("app.services.ifrs.ar.ecl.InvoiceStatus")
    @patch("app.services.ifrs.ar.ecl.Invoice")
    def test_calculate_simplified_with_forward_looking_adjustment(self, mock_invoice_class, mock_status_class):
        """Test simplified calculation with forward-looking adjustment."""
        db = MagicMock()
        org_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        # Configure mock Invoice class to support comparison operators
        mock_invoice_class.balance_due = MagicMock()
        mock_invoice_class.balance_due.__gt__ = MagicMock(return_value=MagicMock())
        mock_invoice_class.organization_id = MagicMock()
        mock_invoice_class.status = MagicMock()
        mock_invoice_class.customer_id = MagicMock()

        calculation_date = date.today()
        invoices = [
            MockInvoice(
                due_date=calculation_date,
                balance_due=Decimal("10000.00"),
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = invoices
        db.query.return_value = mock_query

        # 10% forward-looking adjustment
        input_data = ECLCalculationInput(
            calculation_date=calculation_date,
            fiscal_period_id=fiscal_period_id,
            forward_looking_adjustment=Decimal("0.10"),
        )

        with patch("app.services.ifrs.ar.ecl.ECLService._get_prior_provision") as mock_prior:
            mock_prior.return_value = Decimal("0")

            result = ECLService.calculate_simplified(db, org_id, input_data, user_id)

        assert result is not None
        # Loss rates should be increased by 10%
        current_bucket = next(b for b in result.buckets if b.bucket_name == "CURRENT")
        default_rate = DEFAULT_AGING_BUCKETS["CURRENT"]["default_rate"]
        expected_rate = default_rate * Decimal("1.10")
        assert current_bucket.loss_rate == expected_rate

    @patch("app.services.ifrs.ar.ecl.InvoiceStatus")
    @patch("app.services.ifrs.ar.ecl.Invoice")
    def test_calculate_simplified_for_specific_customer(self, mock_invoice_class, mock_status_class):
        """Test simplified calculation filtered by customer."""
        db = MagicMock()
        org_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()
        customer_id = uuid4()

        # Configure mock Invoice class to support comparison operators
        mock_invoice_class.balance_due = MagicMock()
        mock_invoice_class.balance_due.__gt__ = MagicMock(return_value=MagicMock())
        mock_invoice_class.organization_id = MagicMock()
        mock_invoice_class.status = MagicMock()
        mock_invoice_class.customer_id = MagicMock()

        calculation_date = date.today()
        invoices = [
            MockInvoice(
                customer_id=customer_id,
                due_date=calculation_date,
                balance_due=Decimal("5000.00"),
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = invoices
        db.query.return_value = mock_query

        input_data = ECLCalculationInput(
            calculation_date=calculation_date,
            fiscal_period_id=fiscal_period_id,
            customer_id=customer_id,
        )

        with patch("app.services.ifrs.ar.ecl.ECLService._get_prior_provision") as mock_prior:
            mock_prior.return_value = Decimal("0")

            result = ECLService.calculate_simplified(db, org_id, input_data, user_id)

        assert result is not None

    @patch("app.services.ifrs.ar.ecl.InvoiceStatus")
    @patch("app.services.ifrs.ar.ecl.Invoice")
    def test_calculate_simplified_no_invoices(self, mock_invoice_class, mock_status_class):
        """Test simplified calculation with no outstanding invoices."""
        db = MagicMock()
        org_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        # Configure mock Invoice class to support comparison operators
        mock_invoice_class.balance_due = MagicMock()
        mock_invoice_class.balance_due.__gt__ = MagicMock(return_value=MagicMock())
        mock_invoice_class.organization_id = MagicMock()
        mock_invoice_class.status = MagicMock()
        mock_invoice_class.customer_id = MagicMock()

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = []
        db.query.return_value = mock_query

        input_data = ECLCalculationInput(
            calculation_date=date.today(),
            fiscal_period_id=fiscal_period_id,
        )

        with patch("app.services.ifrs.ar.ecl.ECLService._get_prior_provision") as mock_prior:
            mock_prior.return_value = Decimal("0")

            result = ECLService.calculate_simplified(db, org_id, input_data, user_id)

        assert result is not None
        assert result.total_gross_carrying_amount == Decimal("0")
        assert result.total_provision == Decimal("0")


# ===================== CALCULATE GENERAL TESTS =====================

class TestCalculateGeneral:
    """Tests for general ECL calculation."""

    @patch("app.services.ifrs.ar.ecl.ECLService._get_prior_customer_provision")
    @patch("app.services.ifrs.ar.ecl.ECLStage")
    @patch("app.services.ifrs.ar.ecl.ECLMethodology")
    def test_calculate_general_stage_1(
        self,
        mock_methodology,
        mock_stage_class,
        mock_get_prior,
    ):
        """Test general calculation for Stage 1 (12-month ECL)."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        mock_methodology.GENERAL = MockECLMethodology.GENERAL
        mock_stage_class.STAGE_1 = MockECLStage.STAGE_1
        mock_stage_class.STAGE_2 = MockECLStage.STAGE_2
        mock_stage_class.STAGE_3 = MockECLStage.STAGE_3

        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer

        mock_get_prior.return_value = Decimal("0")

        input_data = GeneralApproachInput(
            calculation_date=date.today(),
            fiscal_period_id=fiscal_period_id,
            customer_id=customer_id,
            probability_of_default=Decimal("0.02"),  # 2% PD
            loss_given_default=Decimal("0.45"),  # 45% LGD
            exposure_at_default=Decimal("100000.00"),  # 100K EAD
            credit_risk_rating="A",
            significant_increase_indicator=False,  # No SICR, so Stage 1
        )

        result = ECLService.calculate_general(db, org_id, input_data, user_id)

        assert result is not None
        db.add.assert_called()
        db.commit.assert_called_once()

    @patch("app.services.ifrs.ar.ecl.ECLService._get_prior_customer_provision")
    @patch("app.services.ifrs.ar.ecl.ECLStage")
    @patch("app.services.ifrs.ar.ecl.ECLMethodology")
    def test_calculate_general_stage_2(
        self,
        mock_methodology,
        mock_stage_class,
        mock_get_prior,
    ):
        """Test general calculation for Stage 2 (lifetime ECL with SICR)."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        mock_methodology.GENERAL = MockECLMethodology.GENERAL
        mock_stage_class.STAGE_1 = MockECLStage.STAGE_1
        mock_stage_class.STAGE_2 = MockECLStage.STAGE_2
        mock_stage_class.STAGE_3 = MockECLStage.STAGE_3

        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer

        mock_get_prior.return_value = Decimal("500.00")

        input_data = GeneralApproachInput(
            calculation_date=date.today(),
            fiscal_period_id=fiscal_period_id,
            customer_id=customer_id,
            probability_of_default=Decimal("0.15"),  # 15% PD (elevated)
            loss_given_default=Decimal("0.50"),
            exposure_at_default=Decimal("50000.00"),
            credit_risk_rating="BB",
            significant_increase_indicator=True,  # SICR triggers Stage 2
        )

        result = ECLService.calculate_general(db, org_id, input_data, user_id)

        assert result is not None

    @patch("app.services.ifrs.ar.ecl.ECLService._get_prior_customer_provision")
    @patch("app.services.ifrs.ar.ecl.ECLStage")
    @patch("app.services.ifrs.ar.ecl.ECLMethodology")
    def test_calculate_general_stage_3(
        self,
        mock_methodology,
        mock_stage_class,
        mock_get_prior,
    ):
        """Test general calculation for Stage 3 (credit-impaired)."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        fiscal_period_id = uuid4()
        user_id = uuid4()

        mock_methodology.GENERAL = MockECLMethodology.GENERAL
        mock_stage_class.STAGE_1 = MockECLStage.STAGE_1
        mock_stage_class.STAGE_2 = MockECLStage.STAGE_2
        mock_stage_class.STAGE_3 = MockECLStage.STAGE_3

        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer

        mock_get_prior.return_value = Decimal("2000.00")

        input_data = GeneralApproachInput(
            calculation_date=date.today(),
            fiscal_period_id=fiscal_period_id,
            customer_id=customer_id,
            probability_of_default=Decimal("0.75"),  # 75% PD (credit-impaired)
            loss_given_default=Decimal("0.60"),
            exposure_at_default=Decimal("25000.00"),
            credit_risk_rating="D",
            significant_increase_indicator=True,  # SICR + high PD = Stage 3
        )

        result = ECLService.calculate_general(db, org_id, input_data, user_id)

        assert result is not None

    def test_calculate_general_customer_not_found(self):
        """Test general calculation with non-existent customer."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        input_data = GeneralApproachInput(
            calculation_date=date.today(),
            fiscal_period_id=uuid4(),
            customer_id=uuid4(),
            probability_of_default=Decimal("0.05"),
            loss_given_default=Decimal("0.40"),
            exposure_at_default=Decimal("10000.00"),
            credit_risk_rating="BBB",
        )

        with pytest.raises(HTTPException) as exc_info:
            ECLService.calculate_general(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 404
        assert "Customer not found" in str(exc_info.value.detail)


# ===================== DETERMINE STAGE TESTS =====================

class TestDetermineStage:
    """Tests for ECL stage determination."""

    def test_stage_1_current(self):
        """Test Stage 1 for current bucket."""
        with patch("app.services.ifrs.ar.ecl.ECLStage") as mock_stage:
            mock_stage.STAGE_1 = MockECLStage.STAGE_1

            result = ECLService._determine_stage("CURRENT")

            assert result == MockECLStage.STAGE_1

    def test_stage_1_1_30_days(self):
        """Test Stage 1 for 1-30 days bucket."""
        with patch("app.services.ifrs.ar.ecl.ECLStage") as mock_stage:
            mock_stage.STAGE_1 = MockECLStage.STAGE_1

            result = ECLService._determine_stage("1_30_DAYS")

            assert result == MockECLStage.STAGE_1

    def test_stage_2_31_60_days(self):
        """Test Stage 2 for 31-60 days bucket."""
        with patch("app.services.ifrs.ar.ecl.ECLStage") as mock_stage:
            mock_stage.STAGE_2 = MockECLStage.STAGE_2

            result = ECLService._determine_stage("31_60_DAYS")

            assert result == MockECLStage.STAGE_2

    def test_stage_2_61_90_days(self):
        """Test Stage 2 for 61-90 days bucket."""
        with patch("app.services.ifrs.ar.ecl.ECLStage") as mock_stage:
            mock_stage.STAGE_2 = MockECLStage.STAGE_2

            result = ECLService._determine_stage("61_90_DAYS")

            assert result == MockECLStage.STAGE_2

    def test_stage_3_91_120_days(self):
        """Test Stage 3 for 91-120 days bucket."""
        with patch("app.services.ifrs.ar.ecl.ECLStage") as mock_stage:
            mock_stage.STAGE_3 = MockECLStage.STAGE_3

            result = ECLService._determine_stage("91_120_DAYS")

            assert result == MockECLStage.STAGE_3

    def test_stage_3_over_120_days(self):
        """Test Stage 3 for over 120 days bucket."""
        with patch("app.services.ifrs.ar.ecl.ECLStage") as mock_stage:
            mock_stage.STAGE_3 = MockECLStage.STAGE_3

            result = ECLService._determine_stage("OVER_120_DAYS")

            assert result == MockECLStage.STAGE_3


# ===================== PRIOR PROVISION TESTS =====================

class TestPriorProvision:
    """Tests for prior provision lookups."""

    def test_get_prior_provision_exists(self):
        """Test getting prior provision when it exists."""
        db = MagicMock()
        org_id = uuid4()
        current_date = date.today()

        # Mock queries - use a chainable mock
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.side_effect = [
            date.today() - timedelta(days=30),  # Max date
            Decimal("500.00"),  # Sum of provisions
        ]
        db.query.return_value = mock_query

        result = ECLService._get_prior_provision(db, org_id, current_date)

        assert result == Decimal("500.00")

    def test_get_prior_provision_none_exists(self):
        """Test getting prior provision when none exists."""
        db = MagicMock()
        org_id = uuid4()
        current_date = date.today()

        # Mock query returning None for max date
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = None
        db.query.return_value = mock_query

        result = ECLService._get_prior_provision(db, org_id, current_date)

        assert result == Decimal("0")

    def test_get_prior_customer_provision_exists(self):
        """Test getting prior customer provision when it exists."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        current_date = date.today()

        mock_prior = MockExpectedCreditLoss(provision_amount=Decimal("200.00"))

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.first.return_value = mock_prior
        db.query.return_value = mock_query

        result = ECLService._get_prior_customer_provision(
            db, org_id, customer_id, current_date
        )

        assert result == Decimal("200.00")

    def test_get_prior_customer_provision_none(self):
        """Test getting prior customer provision when none exists."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        current_date = date.today()

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.first.return_value = None
        db.query.return_value = mock_query

        result = ECLService._get_prior_customer_provision(
            db, org_id, customer_id, current_date
        )

        assert result == Decimal("0")


# ===================== PROVISION SUMMARY TESTS =====================

class TestProvisionSummary:
    """Tests for provision summary."""

    def test_get_provision_summary_with_data(self):
        """Test getting provision summary with existing data."""
        db = MagicMock()
        org_id = uuid4()
        as_of_date = date.today()

        # Mock stage data
        mock_stage1 = MagicMock()
        mock_stage1.ecl_stage = MagicMock()
        mock_stage1.ecl_stage.value = "STAGE_1"
        mock_stage1.gross = Decimal("100000")
        mock_stage1.provision = Decimal("500")

        mock_stage2 = MagicMock()
        mock_stage2.ecl_stage = MagicMock()
        mock_stage2.ecl_stage.value = "STAGE_2"
        mock_stage2.gross = Decimal("20000")
        mock_stage2.provision = Decimal("500")

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = date.today() - timedelta(days=1)  # max_date
        mock_query.group_by.return_value = mock_query
        mock_query.all.return_value = [mock_stage1, mock_stage2]
        db.query.return_value = mock_query

        result = ECLService.get_provision_summary(db, org_id, as_of_date)

        assert result is not None
        assert "as_of_date" in result
        assert "total_provision" in result
        assert "stage_breakdown" in result

    def test_get_provision_summary_no_data(self):
        """Test getting provision summary with no data."""
        db = MagicMock()
        org_id = uuid4()
        as_of_date = date.today()

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.scalar.return_value = None  # No max_date
        db.query.return_value = mock_query

        result = ECLService.get_provision_summary(db, org_id, as_of_date)

        assert result is not None
        assert result["total_provision"] == "0"
        assert result["stage_breakdown"] == {}


# ===================== GETTER TESTS =====================

class TestGetters:
    """Tests for getter methods."""

    @patch("app.services.ifrs.ar.ecl.ExpectedCreditLoss")
    def test_get_ecl_record(self, mock_ecl_class):
        """Test getting ECL record by ID."""
        db = MagicMock()
        ecl_id = uuid4()

        mock_ecl = MockExpectedCreditLoss(ecl_id=ecl_id)
        db.query.return_value.filter.return_value.first.return_value = mock_ecl

        result = ECLService.get(db, str(ecl_id))

        assert result is not None
        assert result.ecl_id == ecl_id

    @patch("app.services.ifrs.ar.ecl.ExpectedCreditLoss")
    def test_get_ecl_record_not_found(self, mock_ecl_class):
        """Test getting non-existent ECL record."""
        db = MagicMock()

        db.query.return_value.filter.return_value.first.return_value = None

        result = ECLService.get(db, str(uuid4()))

        assert result is None


# ===================== LIST TESTS =====================

class TestListECL:
    """Tests for listing ECL records."""

    @patch("app.services.ifrs.ar.ecl.ExpectedCreditLoss")
    def test_list_ecl_records(self, mock_ecl_class):
        """Test listing ECL records."""
        db = MagicMock()

        records = [
            MockExpectedCreditLoss(),
            MockExpectedCreditLoss(),
        ]
        db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = records

        result = ECLService.list(db)

        assert len(result) == 2

    def test_list_ecl_records_with_filters(self):
        """Test listing ECL records with filters."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        fiscal_period_id = uuid4()

        records = [MockExpectedCreditLoss()]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = records
        db.query.return_value = mock_query

        result = ECLService.list(
            db,
            organization_id=str(org_id),
            customer_id=str(customer_id),
            fiscal_period_id=str(fiscal_period_id),
            ecl_stage=MockECLStage.STAGE_1,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            limit=10,
            offset=0,
        )

        assert len(result) == 1
        assert mock_query.filter.called

    @patch("app.services.ifrs.ar.ecl.ExpectedCreditLoss")
    def test_list_ecl_records_empty(self, mock_ecl_class):
        """Test listing returns empty when no records."""
        db = MagicMock()

        db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        result = ECLService.list(db)

        assert len(result) == 0


# ===================== DEFAULT AGING BUCKETS TESTS =====================

class TestDefaultAgingBuckets:
    """Tests for default aging bucket configuration."""

    def test_default_buckets_exist(self):
        """Test that all expected buckets are defined."""
        expected_buckets = [
            "CURRENT",
            "1_30_DAYS",
            "31_60_DAYS",
            "61_90_DAYS",
            "91_120_DAYS",
            "OVER_120_DAYS",
        ]

        for bucket in expected_buckets:
            assert bucket in DEFAULT_AGING_BUCKETS

    def test_buckets_have_required_fields(self):
        """Test that each bucket has required fields."""
        for bucket_name, bucket_info in DEFAULT_AGING_BUCKETS.items():
            assert "days_from" in bucket_info
            assert "days_to" in bucket_info
            assert "default_rate" in bucket_info
            assert isinstance(bucket_info["default_rate"], Decimal)

    def test_loss_rates_increase_with_aging(self):
        """Test that loss rates increase as aging increases."""
        buckets = list(DEFAULT_AGING_BUCKETS.keys())

        for i in range(len(buckets) - 1):
            current_rate = DEFAULT_AGING_BUCKETS[buckets[i]]["default_rate"]
            next_rate = DEFAULT_AGING_BUCKETS[buckets[i + 1]]["default_rate"]
            assert next_rate >= current_rate, f"{buckets[i + 1]} rate should be >= {buckets[i]} rate"
