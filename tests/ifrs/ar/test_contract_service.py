"""
Unit tests for ContractService - IFRS 15 Revenue Recognition.

Tests cover:
- Contract creation with IFRS 15 validation
- Performance obligation management
- Transaction price allocation
- Revenue recognition (over-time and point-in-time)
- Contract modifications
- Contract completion
"""

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.services.finance.ar.contract import (
    ContractService,
    ContractInput,
    PerformanceObligationInput,
    ProgressUpdateInput,
)


# Mock enums
class MockContractType:
    SERVICE = "SERVICE"
    PRODUCT = "PRODUCT"
    SUBSCRIPTION = "SUBSCRIPTION"
    COMBINED = "COMBINED"


class MockContractStatus:
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"

    @property
    def value(self):
        return self


class MockSatisfactionPattern:
    OVER_TIME = "OVER_TIME"
    POINT_IN_TIME = "POINT_IN_TIME"


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


class MockContract:
    """Mock Contract model."""

    def __init__(
        self,
        contract_id=None,
        organization_id=None,
        customer_id=None,
        contract_number="CTR-000001",
        contract_name="Test Contract",
        contract_type=MockContractType.SERVICE,
        start_date=None,
        end_date=None,
        total_contract_value=Decimal("10000.00"),
        currency_code="USD",
        status=None,
        is_enforceable=True,
        has_commercial_substance=True,
        collectability_assessment="PROBABLE",
        significant_financing=False,
        financing_rate=None,
        variable_consideration=None,
        noncash_consideration=None,
        modification_history=None,
        approval_status=None,
    ):
        self.contract_id = contract_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.customer_id = customer_id or uuid4()
        self.contract_number = contract_number
        self.contract_name = contract_name
        self.contract_type = contract_type
        self.start_date = start_date or date.today()
        self.end_date = end_date
        self.total_contract_value = total_contract_value
        self.currency_code = currency_code
        self.status = status or MockContractStatus()
        self.is_enforceable = is_enforceable
        self.has_commercial_substance = has_commercial_substance
        self.collectability_assessment = collectability_assessment
        self.significant_financing = significant_financing
        self.financing_rate = financing_rate
        self.variable_consideration = variable_consideration
        self.noncash_consideration = noncash_consideration
        self.modification_history = modification_history
        self.approval_status = approval_status


class MockPerformanceObligation:
    """Mock PerformanceObligation model."""

    def __init__(
        self,
        obligation_id=None,
        contract_id=None,
        organization_id=None,
        obligation_number=1,
        description="Test Obligation",
        is_distinct=True,
        satisfaction_pattern=None,
        over_time_method=None,
        progress_measure=None,
        standalone_selling_price=Decimal("5000.00"),
        ssp_determination_method="OBSERVABLE",
        allocated_transaction_price=Decimal("5000.00"),
        expected_completion_date=None,
        actual_completion_date=None,
        revenue_account_id=None,
        contract_asset_account_id=None,
        contract_liability_account_id=None,
        status="NOT_STARTED",
        satisfaction_percentage=Decimal("0"),
        total_satisfied_amount=Decimal("0"),
    ):
        self.obligation_id = obligation_id or uuid4()
        self.contract_id = contract_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.obligation_number = obligation_number
        self.description = description
        self.is_distinct = is_distinct
        self.satisfaction_pattern = satisfaction_pattern or MockSatisfactionPattern.POINT_IN_TIME
        self.over_time_method = over_time_method
        self.progress_measure = progress_measure
        self.standalone_selling_price = standalone_selling_price
        self.ssp_determination_method = ssp_determination_method
        self.allocated_transaction_price = allocated_transaction_price
        self.expected_completion_date = expected_completion_date
        self.actual_completion_date = actual_completion_date
        self.revenue_account_id = revenue_account_id or uuid4()
        self.contract_asset_account_id = contract_asset_account_id
        self.contract_liability_account_id = contract_liability_account_id
        self.status = status
        self.satisfaction_percentage = satisfaction_percentage
        self.total_satisfied_amount = total_satisfied_amount


class MockRevenueRecognitionEvent:
    """Mock RevenueRecognitionEvent model."""

    def __init__(
        self,
        event_id=None,
        obligation_id=None,
        organization_id=None,
        event_date=None,
        event_type="SATISFACTION",
        progress_percentage=Decimal("100"),
        amount_recognized=Decimal("5000.00"),
        cumulative_recognized=Decimal("5000.00"),
        measurement_details=None,
    ):
        self.event_id = event_id or uuid4()
        self.obligation_id = obligation_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.event_date = event_date or date.today()
        self.event_type = event_type
        self.progress_percentage = progress_percentage
        self.amount_recognized = amount_recognized
        self.cumulative_recognized = cumulative_recognized
        self.measurement_details = measurement_details


# ===================== CREATE CONTRACT TESTS =====================

class TestCreateContract:
    """Tests for contract creation."""

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    @patch("app.services.finance.ar.contract.Customer")
    def test_create_contract_success(
        self, mock_customer_class, mock_contract_class, mock_obligation_class
    ):
        """Test successful contract creation."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        user_id = uuid4()
        revenue_account_id = uuid4()

        # Mock customer lookup
        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer

        # Mock contract count
        db.query.return_value.filter.return_value.count.return_value = 0

        # Mock contract creation
        mock_contract = MockContract(
            organization_id=org_id,
            customer_id=customer_id,
        )
        mock_contract_class.return_value = mock_contract

        # Create input
        obligation_input = PerformanceObligationInput(
            description="Software License",
            satisfaction_pattern=MockSatisfactionPattern.POINT_IN_TIME,
            standalone_selling_price=Decimal("10000.00"),
            ssp_determination_method="OBSERVABLE",
            revenue_account_id=revenue_account_id,
        )

        input_data = ContractInput(
            customer_id=customer_id,
            contract_name="Software License Agreement",
            contract_type=MockContractType.SERVICE,
            start_date=date.today(),
            currency_code="USD",
            obligations=[obligation_input],
            total_contract_value=Decimal("10000.00"),
        )

        result = ContractService.create_contract(db, org_id, input_data, user_id)

        assert result is not None
        db.add.assert_called()
        db.commit.assert_called_once()

    @patch("app.services.finance.ar.contract.Customer")
    def test_create_contract_customer_not_found(self, mock_customer_class):
        """Test contract creation with non-existent customer."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        user_id = uuid4()

        # Customer not found
        db.query.return_value.filter.return_value.first.return_value = None

        input_data = ContractInput(
            customer_id=customer_id,
            contract_name="Test Contract",
            contract_type=MockContractType.SERVICE,
            start_date=date.today(),
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc_info:
            ContractService.create_contract(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 404
        assert "Customer not found" in str(exc_info.value.detail)

    @patch("app.services.finance.ar.contract.Customer")
    def test_create_contract_not_enforceable(self, mock_customer_class):
        """Test contract creation fails when not enforceable."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        user_id = uuid4()

        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer

        input_data = ContractInput(
            customer_id=customer_id,
            contract_name="Test Contract",
            contract_type=MockContractType.SERVICE,
            start_date=date.today(),
            currency_code="USD",
            is_enforceable=False,  # IFRS 15 requirement fails
        )

        with pytest.raises(HTTPException) as exc_info:
            ContractService.create_contract(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "enforceable" in str(exc_info.value.detail).lower()

    @patch("app.services.finance.ar.contract.Customer")
    def test_create_contract_no_commercial_substance(self, mock_customer_class):
        """Test contract creation fails without commercial substance."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        user_id = uuid4()

        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer

        input_data = ContractInput(
            customer_id=customer_id,
            contract_name="Test Contract",
            contract_type=MockContractType.SERVICE,
            start_date=date.today(),
            currency_code="USD",
            has_commercial_substance=False,  # IFRS 15 requirement fails
        )

        with pytest.raises(HTTPException) as exc_info:
            ContractService.create_contract(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "commercial substance" in str(exc_info.value.detail).lower()

    @patch("app.services.finance.ar.contract.Customer")
    def test_create_contract_collectability_not_probable(self, mock_customer_class):
        """Test contract creation fails when collection not probable."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        user_id = uuid4()

        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer

        input_data = ContractInput(
            customer_id=customer_id,
            contract_name="Test Contract",
            contract_type=MockContractType.SERVICE,
            start_date=date.today(),
            currency_code="USD",
            collectability_assessment="UNLIKELY",  # IFRS 15 requirement fails
        )

        with pytest.raises(HTTPException) as exc_info:
            ContractService.create_contract(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "probable" in str(exc_info.value.detail).lower()

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    @patch("app.services.finance.ar.contract.Customer")
    def test_create_contract_with_multiple_obligations(
        self, mock_customer_class, mock_contract_class, mock_obligation_class
    ):
        """Test contract creation with multiple performance obligations."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()
        user_id = uuid4()
        revenue_account_id = uuid4()

        mock_customer = MockCustomer(customer_id=customer_id, organization_id=org_id)
        db.query.return_value.filter.return_value.first.return_value = mock_customer
        db.query.return_value.filter.return_value.count.return_value = 0

        mock_contract = MockContract(organization_id=org_id, customer_id=customer_id)
        mock_contract_class.return_value = mock_contract

        # Create multiple obligations with different SSPs
        obligations = [
            PerformanceObligationInput(
                description="Software License",
                satisfaction_pattern=MockSatisfactionPattern.POINT_IN_TIME,
                standalone_selling_price=Decimal("6000.00"),
                ssp_determination_method="OBSERVABLE",
                revenue_account_id=revenue_account_id,
            ),
            PerformanceObligationInput(
                description="Implementation Services",
                satisfaction_pattern=MockSatisfactionPattern.OVER_TIME,
                standalone_selling_price=Decimal("4000.00"),
                ssp_determination_method="EXPECTED_COST_PLUS_MARGIN",
                revenue_account_id=revenue_account_id,
                over_time_method="INPUT",
                progress_measure="COST_INCURRED",
            ),
        ]

        input_data = ContractInput(
            customer_id=customer_id,
            contract_name="Software Bundle",
            contract_type=MockContractType.COMBINED,
            start_date=date.today(),
            currency_code="USD",
            obligations=obligations,
            total_contract_value=Decimal("9000.00"),  # Discounted from total SSP
        )

        result = ContractService.create_contract(db, org_id, input_data, user_id)

        assert result is not None
        # Should create contract and add each obligation
        assert db.add.call_count >= 1


# ===================== ACTIVATE CONTRACT TESTS =====================

class TestActivateContract:
    """Tests for contract activation."""

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    def test_activate_contract_success(
        self, mock_contract_class, mock_obligation_class
    ):
        """Test successful contract activation."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()
        user_id = uuid4()

        # Create mock contract in DRAFT status
        mock_status = MagicMock()
        mock_status.value = "DRAFT"

        mock_contract = MockContract(
            contract_id=contract_id,
            organization_id=org_id,
        )
        mock_contract.status = mock_status

        # Mock queries
        db.query.return_value.filter.return_value.first.return_value = mock_contract
        db.query.return_value.filter.return_value.count.return_value = 1  # Has obligations

        # Set up status comparison
        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.DRAFT = mock_status
            mock_status_class.ACTIVE = MagicMock()
            mock_status_class.ACTIVE.value = "ACTIVE"

            result = ContractService.activate_contract(db, org_id, contract_id, user_id)

        assert result is not None
        db.commit.assert_called_once()

    @patch("app.services.finance.ar.contract.Contract")
    def test_activate_contract_not_found(self, mock_contract_class):
        """Test activating non-existent contract."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()
        user_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ContractService.activate_contract(db, org_id, contract_id, user_id)

        assert exc_info.value.status_code == 404
        assert "Contract not found" in str(exc_info.value.detail)

    @patch("app.services.finance.ar.contract.Contract")
    def test_activate_contract_wrong_status(self, mock_contract_class):
        """Test activating contract that's not in DRAFT status."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()
        user_id = uuid4()

        mock_status = MagicMock()
        mock_status.value = "ACTIVE"

        mock_contract = MockContract(contract_id=contract_id, organization_id=org_id)
        mock_contract.status = mock_status

        db.query.return_value.filter.return_value.first.return_value = mock_contract

        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.DRAFT = MagicMock()
            mock_status_class.DRAFT.value = "DRAFT"

            with pytest.raises(HTTPException) as exc_info:
                ContractService.activate_contract(db, org_id, contract_id, user_id)

        assert exc_info.value.status_code == 400
        assert "Cannot activate" in str(exc_info.value.detail)

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    def test_activate_contract_no_obligations(
        self, mock_contract_class, mock_obligation_class
    ):
        """Test activating contract without performance obligations."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()
        user_id = uuid4()

        mock_status = MagicMock()
        mock_status.value = "DRAFT"

        mock_contract = MockContract(contract_id=contract_id, organization_id=org_id)
        mock_contract.status = mock_status

        # First call returns contract, second call returns count of 0
        db.query.return_value.filter.return_value.first.return_value = mock_contract
        db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.DRAFT = mock_status

            with pytest.raises(HTTPException) as exc_info:
                ContractService.activate_contract(db, org_id, contract_id, user_id)

        assert exc_info.value.status_code == 400
        assert "performance obligation" in str(exc_info.value.detail).lower()


# ===================== ADD PERFORMANCE OBLIGATION TESTS =====================

class TestAddPerformanceObligation:
    """Tests for adding performance obligations."""

    @patch("app.services.finance.ar.contract.ContractService.reallocate_transaction_price")
    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.ContractStatus")
    @patch("app.services.finance.ar.contract.Contract")
    def test_add_obligation_success(
        self, mock_contract_class, mock_status_class, mock_obligation_class, mock_reallocate
    ):
        """Test successfully adding a performance obligation."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()
        revenue_account_id = uuid4()

        # Create mock statuses
        mock_draft = MagicMock()
        mock_active = MagicMock()
        mock_status_class.DRAFT = mock_draft
        mock_status_class.ACTIVE = mock_active

        mock_contract = MockContract(contract_id=contract_id, organization_id=org_id)
        mock_contract.status = mock_draft  # Contract is in DRAFT status

        db.query.return_value.filter.return_value.first.return_value = mock_contract
        db.query.return_value.filter.return_value.count.return_value = 1  # Existing count

        mock_obligation = MockPerformanceObligation(
            contract_id=contract_id,
            organization_id=org_id,
        )
        mock_obligation_class.return_value = mock_obligation

        input_data = PerformanceObligationInput(
            description="Support Services",
            satisfaction_pattern=MockSatisfactionPattern.OVER_TIME,
            standalone_selling_price=Decimal("3000.00"),
            ssp_determination_method="RESIDUAL",
            revenue_account_id=revenue_account_id,
        )

        result = ContractService.add_performance_obligation(
            db, org_id, contract_id, input_data
        )

        assert result is not None
        db.add.assert_called()
        db.commit.assert_called()
        mock_reallocate.assert_called_once()

    @patch("app.services.finance.ar.contract.Contract")
    def test_add_obligation_contract_not_found(self, mock_contract_class):
        """Test adding obligation to non-existent contract."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        input_data = PerformanceObligationInput(
            description="Test",
            satisfaction_pattern=MockSatisfactionPattern.POINT_IN_TIME,
            standalone_selling_price=Decimal("1000.00"),
            ssp_determination_method="OBSERVABLE",
            revenue_account_id=uuid4(),
        )

        with pytest.raises(HTTPException) as exc_info:
            ContractService.add_performance_obligation(db, org_id, contract_id, input_data)

        assert exc_info.value.status_code == 404

    @patch("app.services.finance.ar.contract.Contract")
    def test_add_obligation_wrong_status(self, mock_contract_class):
        """Test adding obligation to completed contract."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        mock_status = MagicMock()
        mock_status.value = "COMPLETED"

        mock_contract = MockContract(contract_id=contract_id, organization_id=org_id)
        mock_contract.status = mock_status

        db.query.return_value.filter.return_value.first.return_value = mock_contract

        input_data = PerformanceObligationInput(
            description="Test",
            satisfaction_pattern=MockSatisfactionPattern.POINT_IN_TIME,
            standalone_selling_price=Decimal("1000.00"),
            ssp_determination_method="OBSERVABLE",
            revenue_account_id=uuid4(),
        )

        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.DRAFT = MagicMock()
            mock_status_class.ACTIVE = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                ContractService.add_performance_obligation(
                    db, org_id, contract_id, input_data
                )

        assert exc_info.value.status_code == 400


# ===================== REALLOCATE TRANSACTION PRICE TESTS =====================

class TestReallocateTransactionPrice:
    """Tests for transaction price reallocation."""

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    def test_reallocate_transaction_price_success(
        self, mock_contract_class, mock_obligation_class
    ):
        """Test successful transaction price reallocation."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        # Create mock contract
        mock_contract = MockContract(
            contract_id=contract_id,
            organization_id=org_id,
            total_contract_value=Decimal("9000.00"),
        )

        # Create mock obligations with different SSPs
        obligation1 = MockPerformanceObligation(
            contract_id=contract_id,
            standalone_selling_price=Decimal("6000.00"),
        )
        obligation2 = MockPerformanceObligation(
            contract_id=contract_id,
            standalone_selling_price=Decimal("4000.00"),
        )

        db.query.return_value.filter.return_value.first.return_value = mock_contract
        db.query.return_value.filter.return_value.all.return_value = [
            obligation1, obligation2
        ]

        ContractService.reallocate_transaction_price(db, org_id, contract_id)

        # Should allocate based on relative SSP
        # Total SSP = 10000, Contract = 9000
        # Obligation 1: 6000/10000 * 9000 = 5400
        # Obligation 2: 4000/10000 * 9000 = 3600
        assert obligation1.allocated_transaction_price == Decimal("5400.00")
        assert obligation2.allocated_transaction_price == Decimal("3600.00")
        db.commit.assert_called_once()

    @patch("app.services.finance.ar.contract.Contract")
    def test_reallocate_contract_not_found(self, mock_contract_class):
        """Test reallocation when contract not found."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        # Should not raise, just return
        ContractService.reallocate_transaction_price(db, org_id, contract_id)

        db.commit.assert_not_called()

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    def test_reallocate_no_obligations(
        self, mock_contract_class, mock_obligation_class
    ):
        """Test reallocation with no obligations."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        mock_contract = MockContract(
            contract_id=contract_id,
            organization_id=org_id,
        )

        db.query.return_value.filter.return_value.first.return_value = mock_contract
        db.query.return_value.filter.return_value.all.return_value = []

        ContractService.reallocate_transaction_price(db, org_id, contract_id)

        db.commit.assert_not_called()


# ===================== UPDATE PROGRESS TESTS =====================

class TestUpdateProgress:
    """Tests for over-time revenue recognition progress updates."""

    @patch("app.services.finance.ar.contract.RevenueRecognitionEvent")
    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_update_progress_success(
        self, mock_obligation_class, mock_event_class
    ):
        """Test successful progress update."""
        db = MagicMock()
        org_id = uuid4()
        obligation_id = uuid4()
        user_id = uuid4()

        # Create over-time obligation
        mock_obligation = MockPerformanceObligation(
            obligation_id=obligation_id,
            organization_id=org_id,
            satisfaction_pattern=MockSatisfactionPattern.OVER_TIME,
            allocated_transaction_price=Decimal("10000.00"),
            total_satisfied_amount=Decimal("0"),
            status="NOT_STARTED",
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        mock_event = MockRevenueRecognitionEvent(obligation_id=obligation_id)
        mock_event_class.return_value = mock_event

        input_data = ProgressUpdateInput(
            obligation_id=obligation_id,
            event_date=date.today(),
            progress_percentage=Decimal("25"),
        )

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.OVER_TIME = MockSatisfactionPattern.OVER_TIME

            result = ContractService.update_progress(db, org_id, input_data, user_id)

        assert result is not None
        db.add.assert_called()
        db.commit.assert_called_once()

        # Check obligation was updated
        assert mock_obligation.satisfaction_percentage == Decimal("25")
        assert mock_obligation.status == "IN_PROGRESS"

    @patch("app.services.finance.ar.contract.RevenueRecognitionEvent")
    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_update_progress_to_100_satisfies_obligation(
        self, mock_obligation_class, mock_event_class
    ):
        """Test that 100% progress marks obligation as satisfied."""
        db = MagicMock()
        org_id = uuid4()
        obligation_id = uuid4()
        user_id = uuid4()

        mock_obligation = MockPerformanceObligation(
            obligation_id=obligation_id,
            organization_id=org_id,
            satisfaction_pattern=MockSatisfactionPattern.OVER_TIME,
            allocated_transaction_price=Decimal("10000.00"),
            total_satisfied_amount=Decimal("7500.00"),
            satisfaction_percentage=Decimal("75"),
            status="IN_PROGRESS",
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        mock_event = MockRevenueRecognitionEvent(obligation_id=obligation_id)
        mock_event_class.return_value = mock_event

        input_data = ProgressUpdateInput(
            obligation_id=obligation_id,
            event_date=date.today(),
            progress_percentage=Decimal("100"),
        )

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.OVER_TIME = MockSatisfactionPattern.OVER_TIME

            result = ContractService.update_progress(db, org_id, input_data, user_id)

        assert result is not None
        assert mock_obligation.status == "SATISFIED"
        assert mock_obligation.actual_completion_date == date.today()

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_update_progress_obligation_not_found(self, mock_obligation_class):
        """Test progress update for non-existent obligation."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        input_data = ProgressUpdateInput(
            obligation_id=uuid4(),
            event_date=date.today(),
            progress_percentage=Decimal("50"),
        )

        with pytest.raises(HTTPException) as exc_info:
            ContractService.update_progress(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 404

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_update_progress_point_in_time_fails(self, mock_obligation_class):
        """Test that progress updates fail for point-in-time obligations."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        mock_obligation = MockPerformanceObligation(
            satisfaction_pattern=MockSatisfactionPattern.POINT_IN_TIME,
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        input_data = ProgressUpdateInput(
            obligation_id=uuid4(),
            event_date=date.today(),
            progress_percentage=Decimal("50"),
        )

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.OVER_TIME = MagicMock()  # Different from POINT_IN_TIME

            with pytest.raises(HTTPException) as exc_info:
                ContractService.update_progress(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "over-time" in str(exc_info.value.detail).lower()

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_update_progress_already_satisfied(self, mock_obligation_class):
        """Test progress update fails for already satisfied obligation."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        mock_obligation = MockPerformanceObligation(
            satisfaction_pattern=MockSatisfactionPattern.OVER_TIME,
            status="SATISFIED",
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        input_data = ProgressUpdateInput(
            obligation_id=uuid4(),
            event_date=date.today(),
            progress_percentage=Decimal("50"),
        )

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.OVER_TIME = MockSatisfactionPattern.OVER_TIME

            with pytest.raises(HTTPException) as exc_info:
                ContractService.update_progress(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "satisfied" in str(exc_info.value.detail).lower()

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_update_progress_cannot_decrease(self, mock_obligation_class):
        """Test progress cannot decrease."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        mock_obligation = MockPerformanceObligation(
            satisfaction_pattern=MockSatisfactionPattern.OVER_TIME,
            allocated_transaction_price=Decimal("10000.00"),
            total_satisfied_amount=Decimal("5000.00"),  # 50% already recognized
            satisfaction_percentage=Decimal("50"),
            status="IN_PROGRESS",
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        input_data = ProgressUpdateInput(
            obligation_id=uuid4(),
            event_date=date.today(),
            progress_percentage=Decimal("40"),  # Trying to decrease
        )

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.OVER_TIME = MockSatisfactionPattern.OVER_TIME

            with pytest.raises(HTTPException) as exc_info:
                ContractService.update_progress(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "decrease" in str(exc_info.value.detail).lower()


# ===================== SATISFY POINT IN TIME TESTS =====================

class TestSatisfyPointInTime:
    """Tests for point-in-time revenue recognition."""

    @patch("app.services.finance.ar.contract.RevenueRecognitionEvent")
    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_satisfy_point_in_time_success(
        self, mock_obligation_class, mock_event_class
    ):
        """Test successful point-in-time satisfaction."""
        db = MagicMock()
        org_id = uuid4()
        obligation_id = uuid4()
        user_id = uuid4()

        mock_obligation = MockPerformanceObligation(
            obligation_id=obligation_id,
            organization_id=org_id,
            satisfaction_pattern=MockSatisfactionPattern.POINT_IN_TIME,
            allocated_transaction_price=Decimal("5000.00"),
            total_satisfied_amount=Decimal("0"),
            status="NOT_STARTED",
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        mock_event = MockRevenueRecognitionEvent(
            obligation_id=obligation_id,
            amount_recognized=Decimal("5000.00"),
        )
        mock_event_class.return_value = mock_event

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.POINT_IN_TIME = MockSatisfactionPattern.POINT_IN_TIME

            result = ContractService.satisfy_point_in_time(
                db, org_id, obligation_id, date.today(), user_id
            )

        assert result is not None
        assert mock_obligation.status == "SATISFIED"
        assert mock_obligation.satisfaction_percentage == Decimal("100")
        assert mock_obligation.total_satisfied_amount == Decimal("5000.00")
        db.add.assert_called()
        db.commit.assert_called_once()

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_satisfy_point_in_time_not_found(self, mock_obligation_class):
        """Test satisfaction of non-existent obligation."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ContractService.satisfy_point_in_time(
                db, org_id, uuid4(), date.today(), user_id
            )

        assert exc_info.value.status_code == 404

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_satisfy_point_in_time_wrong_pattern(self, mock_obligation_class):
        """Test point-in-time fails for over-time obligations."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        mock_obligation = MockPerformanceObligation(
            satisfaction_pattern=MockSatisfactionPattern.OVER_TIME,
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.POINT_IN_TIME = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                ContractService.satisfy_point_in_time(
                    db, org_id, uuid4(), date.today(), user_id
                )

        assert exc_info.value.status_code == 400
        assert "over-time" in str(exc_info.value.detail).lower()

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_satisfy_point_in_time_already_satisfied(self, mock_obligation_class):
        """Test cannot satisfy already satisfied obligation."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        mock_obligation = MockPerformanceObligation(
            satisfaction_pattern=MockSatisfactionPattern.POINT_IN_TIME,
            status="SATISFIED",
        )

        db.query.return_value.filter.return_value.first.return_value = mock_obligation

        with patch("app.services.finance.ar.contract.SatisfactionPattern") as mock_pattern:
            mock_pattern.POINT_IN_TIME = MockSatisfactionPattern.POINT_IN_TIME

            with pytest.raises(HTTPException) as exc_info:
                ContractService.satisfy_point_in_time(
                    db, org_id, uuid4(), date.today(), user_id
                )

        assert exc_info.value.status_code == 400
        assert "satisfied" in str(exc_info.value.detail).lower()


# ===================== MODIFY CONTRACT TESTS =====================

class TestModifyContract:
    """Tests for contract modifications."""

    @patch("app.services.finance.ar.contract.ContractService._reallocate_prospectively")
    @patch("app.services.finance.ar.contract.Contract")
    def test_modify_contract_prospective(
        self, mock_contract_class, mock_reallocate
    ):
        """Test prospective contract modification."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        mock_status = MagicMock()
        mock_status.value = "ACTIVE"

        mock_contract = MockContract(
            contract_id=contract_id,
            organization_id=org_id,
            total_contract_value=Decimal("10000.00"),
            modification_history=None,
        )
        mock_contract.status = mock_status

        db.query.return_value.filter.return_value.first.return_value = mock_contract

        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.ACTIVE = mock_status

            result = ContractService.modify_contract(
                db,
                org_id,
                contract_id,
                modification_date=date.today(),
                new_transaction_price=Decimal("12000.00"),
                modification_type="PROSPECTIVE",
            )

        assert result is not None
        assert mock_contract.total_contract_value == Decimal("12000.00")
        assert mock_contract.modification_history is not None
        mock_reallocate.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.services.finance.ar.contract.ContractService._reallocate_cumulative_catchup")
    @patch("app.services.finance.ar.contract.Contract")
    def test_modify_contract_cumulative_catchup(
        self, mock_contract_class, mock_reallocate
    ):
        """Test cumulative catch-up contract modification."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        mock_status = MagicMock()
        mock_status.value = "ACTIVE"

        mock_contract = MockContract(
            contract_id=contract_id,
            organization_id=org_id,
            total_contract_value=Decimal("10000.00"),
            modification_history={"modifications": []},
        )
        mock_contract.status = mock_status

        db.query.return_value.filter.return_value.first.return_value = mock_contract

        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.ACTIVE = mock_status

            result = ContractService.modify_contract(
                db,
                org_id,
                contract_id,
                modification_date=date.today(),
                new_transaction_price=Decimal("15000.00"),
                modification_type="CUMULATIVE_CATCHUP",
            )

        assert result is not None
        assert mock_contract.total_contract_value == Decimal("15000.00")
        mock_reallocate.assert_called_once()

    @patch("app.services.finance.ar.contract.Contract")
    def test_modify_contract_not_found(self, mock_contract_class):
        """Test modifying non-existent contract."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ContractService.modify_contract(
                db, org_id, contract_id, date.today()
            )

        assert exc_info.value.status_code == 404

    @patch("app.services.finance.ar.contract.Contract")
    def test_modify_contract_wrong_status(self, mock_contract_class):
        """Test modifying contract not in ACTIVE status."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        mock_status = MagicMock()
        mock_status.value = "COMPLETED"

        mock_contract = MockContract(contract_id=contract_id, organization_id=org_id)
        mock_contract.status = mock_status

        db.query.return_value.filter.return_value.first.return_value = mock_contract

        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.ACTIVE = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                ContractService.modify_contract(
                    db, org_id, contract_id, date.today()
                )

        assert exc_info.value.status_code == 400


# ===================== COMPLETE CONTRACT TESTS =====================

class TestCompleteContract:
    """Tests for contract completion."""

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    def test_complete_contract_success(
        self, mock_contract_class, mock_obligation_class
    ):
        """Test successful contract completion."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        mock_contract = MockContract(contract_id=contract_id, organization_id=org_id)

        db.query.return_value.filter.return_value.first.return_value = mock_contract
        db.query.return_value.filter.return_value.count.return_value = 0  # No unsatisfied

        with patch("app.services.finance.ar.contract.ContractStatus") as mock_status_class:
            mock_status_class.COMPLETED = MagicMock()

            result = ContractService.complete_contract(db, org_id, contract_id)

        assert result is not None
        db.commit.assert_called_once()

    @patch("app.services.finance.ar.contract.Contract")
    def test_complete_contract_not_found(self, mock_contract_class):
        """Test completing non-existent contract."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            ContractService.complete_contract(db, org_id, contract_id)

        assert exc_info.value.status_code == 404

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    @patch("app.services.finance.ar.contract.Contract")
    def test_complete_contract_unsatisfied_obligations(
        self, mock_contract_class, mock_obligation_class
    ):
        """Test cannot complete contract with unsatisfied obligations."""
        db = MagicMock()
        org_id = uuid4()
        contract_id = uuid4()

        mock_contract = MockContract(contract_id=contract_id, organization_id=org_id)

        db.query.return_value.filter.return_value.first.return_value = mock_contract
        db.query.return_value.filter.return_value.count.return_value = 2  # 2 unsatisfied

        with pytest.raises(HTTPException) as exc_info:
            ContractService.complete_contract(db, org_id, contract_id)

        assert exc_info.value.status_code == 400
        assert "2 performance obligations" in str(exc_info.value.detail)


# ===================== GETTER TESTS =====================

class TestGetters:
    """Tests for getter methods."""

    @patch("app.services.finance.ar.contract.Contract")
    def test_get_contract(self, mock_contract_class):
        """Test getting contract by ID."""
        db = MagicMock()
        contract_id = uuid4()

        mock_contract = MockContract(contract_id=contract_id)
        db.query.return_value.filter.return_value.first.return_value = mock_contract

        result = ContractService.get(db, str(contract_id))

        assert result is not None
        assert result.contract_id == contract_id

    @patch("app.services.finance.ar.contract.Contract")
    def test_get_contract_not_found(self, mock_contract_class):
        """Test getting non-existent contract."""
        db = MagicMock()

        db.query.return_value.filter.return_value.first.return_value = None

        result = ContractService.get(db, str(uuid4()))

        assert result is None

    @patch("app.services.finance.ar.contract.Contract")
    def test_get_by_number(self, mock_contract_class):
        """Test getting contract by number."""
        db = MagicMock()
        org_id = uuid4()

        mock_contract = MockContract(contract_number="CTR-000001")
        db.query.return_value.filter.return_value.first.return_value = mock_contract

        result = ContractService.get_by_number(db, org_id, "CTR-000001")

        assert result is not None
        assert result.contract_number == "CTR-000001"

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_get_obligations(self, mock_obligation_class):
        """Test getting obligations for a contract."""
        db = MagicMock()
        contract_id = uuid4()

        obligations = [
            MockPerformanceObligation(obligation_number=1),
            MockPerformanceObligation(obligation_number=2),
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = obligations

        result = ContractService.get_obligations(db, str(contract_id))

        assert len(result) == 2

    @patch("app.services.finance.ar.contract.RevenueRecognitionEvent")
    def test_get_recognition_events(self, mock_event_class):
        """Test getting recognition events for an obligation."""
        db = MagicMock()
        obligation_id = uuid4()

        events = [
            MockRevenueRecognitionEvent(progress_percentage=Decimal("50")),
            MockRevenueRecognitionEvent(progress_percentage=Decimal("100")),
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = events

        result = ContractService.get_recognition_events(db, str(obligation_id))

        assert len(result) == 2


# ===================== LIST TESTS =====================

class TestListContracts:
    """Tests for listing contracts."""

    @patch("app.services.finance.ar.contract.Contract")
    def test_list_contracts(self, mock_contract_class):
        """Test listing contracts."""
        db = MagicMock()

        contracts = [
            MockContract(contract_number="CTR-000001"),
            MockContract(contract_number="CTR-000002"),
        ]
        db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = contracts

        result = ContractService.list(db)

        assert len(result) == 2

    def test_list_contracts_with_filters(self):
        """Test listing contracts with filters."""
        db = MagicMock()
        org_id = uuid4()
        customer_id = uuid4()

        contracts = [MockContract()]

        # Create a mock query builder that returns itself for chained calls
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = contracts
        db.query.return_value = mock_query

        result = ContractService.list(
            db,
            organization_id=str(org_id),
            customer_id=str(customer_id),
            status=MockContractStatus.ACTIVE,
            contract_type=MockContractType.SERVICE,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            limit=10,
            offset=0,
        )

        assert len(result) == 1
        # Verify filters were applied
        assert mock_query.filter.called

    @patch("app.services.finance.ar.contract.Contract")
    def test_list_contracts_empty(self, mock_contract_class):
        """Test listing returns empty when no contracts."""
        db = MagicMock()

        db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        result = ContractService.list(db)

        assert len(result) == 0


# ===================== INTERNAL REALLOCATION TESTS =====================

class TestInternalReallocation:
    """Tests for internal reallocation methods."""

    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_reallocate_prospectively(self, mock_obligation_class):
        """Test prospective reallocation."""
        db = MagicMock()

        # Create contract with total value
        mock_contract = MockContract(
            total_contract_value=Decimal("8000.00"),
        )

        # Create satisfied obligation
        satisfied = MockPerformanceObligation(
            status="SATISFIED",
            allocated_transaction_price=Decimal("4000.00"),
        )

        # Create unsatisfied obligations
        unsatisfied1 = MockPerformanceObligation(
            status="IN_PROGRESS",
            standalone_selling_price=Decimal("3000.00"),
        )
        unsatisfied2 = MockPerformanceObligation(
            status="NOT_STARTED",
            standalone_selling_price=Decimal("2000.00"),
        )

        # Setup query returns
        db.query.return_value.filter.return_value.all.side_effect = [
            [unsatisfied1, unsatisfied2],  # First call - unsatisfied
            [satisfied],  # Second call - satisfied
        ]

        ContractService._reallocate_prospectively(db, mock_contract)

        # Remaining = 8000 - 4000 = 4000
        # Total unsatisfied SSP = 5000
        # unsatisfied1: 3000/5000 * 4000 = 2400
        # unsatisfied2: 2000/5000 * 4000 = 1600
        assert unsatisfied1.allocated_transaction_price == Decimal("2400.00")
        assert unsatisfied2.allocated_transaction_price == Decimal("1600.00")

    @patch("app.services.finance.ar.contract.RevenueRecognitionEvent")
    @patch("app.services.finance.ar.contract.PerformanceObligation")
    def test_reallocate_cumulative_catchup(
        self, mock_obligation_class, mock_event_class
    ):
        """Test cumulative catch-up reallocation."""
        db = MagicMock()

        mock_contract = MockContract(
            contract_id=uuid4(),
            organization_id=uuid4(),
            total_contract_value=Decimal("12000.00"),  # Increased from 10000
        )

        # Obligation at 50% complete
        obligation = MockPerformanceObligation(
            obligation_id=uuid4(),
            standalone_selling_price=Decimal("10000.00"),
            allocated_transaction_price=Decimal("10000.00"),
            satisfaction_percentage=Decimal("50"),
            total_satisfied_amount=Decimal("5000.00"),  # 50% of 10000
        )

        db.query.return_value.filter.return_value.all.return_value = [obligation]

        mock_event = MockRevenueRecognitionEvent()
        mock_event_class.return_value = mock_event

        ContractService._reallocate_cumulative_catchup(db, mock_contract)

        # New allocation: 12000 (only one obligation)
        # Expected recognized at 50%: 6000
        # Already recognized: 5000
        # Catch-up: 1000
        assert obligation.allocated_transaction_price == Decimal("12000.00")
        assert obligation.total_satisfied_amount == Decimal("6000.00")
        db.add.assert_called()  # Catch-up event created
