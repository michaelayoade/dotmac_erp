"""
Fixtures for Lease module tests.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# Import actual enums from models
from app.models.finance.lease.lease_contract import LeaseClassification, LeaseStatus
from app.models.finance.lease.lease_modification import ModificationType
from app.models.finance.lease.lease_payment_schedule import PaymentStatus

# ============ Mock Models ============


class MockLeaseContract:
    """Mock LeaseContract model."""

    def __init__(
        self,
        lease_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        lease_number: str = "LEASE-0001",
        lease_name: str = "Office Lease",
        description: str | None = None,
        lessor_supplier_id: uuid.UUID | None = None,
        lessor_name: str = "Property Holdings Inc.",
        external_reference: str | None = None,
        classification: LeaseClassification = LeaseClassification.FINANCE,
        is_lessee: bool = True,
        commencement_date: date = None,
        end_date: date = None,
        lease_term_months: int = 60,
        has_renewal_option: bool = False,
        renewal_option_term_months: int | None = None,
        renewal_reasonably_certain: bool = False,
        has_purchase_option: bool = False,
        purchase_option_price: Decimal | None = None,
        purchase_reasonably_certain: bool = False,
        has_termination_option: bool = False,
        termination_penalty: Decimal | None = None,
        currency_code: str = "USD",
        payment_frequency: str = "MONTHLY",
        payment_timing: str = "ADVANCE",
        base_payment_amount: Decimal = Decimal("5000.00"),
        has_variable_payments: bool = False,
        variable_payment_basis: str | None = None,
        is_index_linked: bool = False,
        index_type: str | None = None,
        index_base_value: Decimal | None = None,
        residual_value_guarantee: Decimal = Decimal("0"),
        incremental_borrowing_rate: Decimal = Decimal("0.05"),
        implicit_rate_known: bool = False,
        implicit_rate: Decimal | None = None,
        discount_rate_used: Decimal = Decimal("0.05"),
        initial_direct_costs: Decimal = Decimal("0"),
        lease_incentives_received: Decimal = Decimal("0"),
        restoration_obligation: Decimal = Decimal("0"),
        asset_description: str = "Office Space",
        asset_category_id: uuid.UUID | None = None,
        location_id: uuid.UUID | None = None,
        status: LeaseStatus = LeaseStatus.DRAFT,
        cost_center_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID = None,
        approved_by_user_id: uuid.UUID | None = None,
        approved_at: datetime | None = None,
        created_at: datetime = None,
        updated_at: datetime | None = None,
        **kwargs,
    ):
        self.lease_id = lease_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.lease_number = lease_number
        self.lease_name = lease_name
        self.description = description
        self.lessor_supplier_id = lessor_supplier_id
        self.lessor_name = lessor_name
        self.external_reference = external_reference
        self.classification = classification
        self.is_lessee = is_lessee
        self.commencement_date = commencement_date or date(2024, 1, 1)
        self.end_date = end_date or date(2028, 12, 31)
        self.lease_term_months = lease_term_months
        self.has_renewal_option = has_renewal_option
        self.renewal_option_term_months = renewal_option_term_months
        self.renewal_reasonably_certain = renewal_reasonably_certain
        self.has_purchase_option = has_purchase_option
        self.purchase_option_price = purchase_option_price
        self.purchase_reasonably_certain = purchase_reasonably_certain
        self.has_termination_option = has_termination_option
        self.termination_penalty = termination_penalty
        self.currency_code = currency_code
        self.payment_frequency = payment_frequency
        self.payment_timing = payment_timing
        self.base_payment_amount = base_payment_amount
        self.has_variable_payments = has_variable_payments
        self.variable_payment_basis = variable_payment_basis
        self.is_index_linked = is_index_linked
        self.index_type = index_type
        self.index_base_value = index_base_value
        self.residual_value_guarantee = residual_value_guarantee
        self.incremental_borrowing_rate = incremental_borrowing_rate
        self.implicit_rate_known = implicit_rate_known
        self.implicit_rate = implicit_rate
        self.discount_rate_used = discount_rate_used
        self.initial_direct_costs = initial_direct_costs
        self.lease_incentives_received = lease_incentives_received
        self.restoration_obligation = restoration_obligation
        self.asset_description = asset_description
        self.asset_category_id = asset_category_id
        self.location_id = location_id
        self.status = status
        self.cost_center_id = cost_center_id
        self.project_id = project_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockLeaseLiability:
    """Mock LeaseLiability model."""

    def __init__(
        self,
        liability_id: uuid.UUID = None,
        lease_id: uuid.UUID = None,
        initial_measurement_date: date = None,
        initial_liability_amount: Decimal = Decimal("250000.00"),
        pv_fixed_payments: Decimal = Decimal("250000.00"),
        pv_variable_payments: Decimal = Decimal("0"),
        pv_residual_guarantee: Decimal = Decimal("0"),
        pv_purchase_option: Decimal = Decimal("0"),
        pv_termination_penalties: Decimal = Decimal("0"),
        discount_rate: Decimal = Decimal("0.05"),
        current_liability_balance: Decimal = Decimal("250000.00"),
        total_interest_expense: Decimal = Decimal("0"),
        total_payments_made: Decimal = Decimal("0"),
        modification_adjustments: Decimal = Decimal("0"),
        current_portion: Decimal = Decimal("50000.00"),
        non_current_portion: Decimal = Decimal("200000.00"),
        lease_liability_account_id: uuid.UUID = None,
        interest_expense_account_id: uuid.UUID = None,
        last_interest_date: date | None = None,
        last_interest_period_id: uuid.UUID | None = None,
        created_at: datetime = None,
        updated_at: datetime | None = None,
        incremental_borrowing_rate: Decimal = Decimal("0.05"),
        **kwargs,
    ):
        self.liability_id = liability_id or uuid.uuid4()
        self.lease_id = lease_id or uuid.uuid4()
        self.initial_measurement_date = initial_measurement_date or date(2024, 1, 1)
        self.initial_liability_amount = initial_liability_amount
        self.pv_fixed_payments = pv_fixed_payments
        self.pv_variable_payments = pv_variable_payments
        self.pv_residual_guarantee = pv_residual_guarantee
        self.pv_purchase_option = pv_purchase_option
        self.pv_termination_penalties = pv_termination_penalties
        self.discount_rate = discount_rate
        self.current_liability_balance = current_liability_balance
        self.total_interest_expense = total_interest_expense
        self.total_payments_made = total_payments_made
        self.modification_adjustments = modification_adjustments
        self.current_portion = current_portion
        self.non_current_portion = non_current_portion
        self.lease_liability_account_id = lease_liability_account_id or uuid.uuid4()
        self.interest_expense_account_id = interest_expense_account_id or uuid.uuid4()
        self.last_interest_date = last_interest_date
        self.last_interest_period_id = last_interest_period_id
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at
        self.incremental_borrowing_rate = incremental_borrowing_rate
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockLeaseAsset:
    """Mock LeaseAsset model."""

    def __init__(
        self,
        asset_id: uuid.UUID = None,
        lease_id: uuid.UUID = None,
        initial_measurement_date: date = None,
        lease_liability_at_commencement: Decimal = Decimal("250000.00"),
        lease_payments_at_commencement: Decimal = Decimal("0"),
        initial_direct_costs: Decimal = Decimal("5000.00"),
        restoration_obligation: Decimal = Decimal("10000.00"),
        lease_incentives_deducted: Decimal = Decimal("0"),
        initial_rou_asset_value: Decimal = Decimal("265000.00"),
        depreciation_method: str = "STRAIGHT_LINE",
        useful_life_months: int = 60,
        residual_value: Decimal = Decimal("0"),
        accumulated_depreciation: Decimal = Decimal("0"),
        impairment_losses: Decimal = Decimal("0"),
        revaluation_adjustments: Decimal = Decimal("0"),
        modification_adjustments: Decimal = Decimal("0"),
        carrying_amount: Decimal = Decimal("265000.00"),
        rou_asset_account_id: uuid.UUID = None,
        accumulated_depreciation_account_id: uuid.UUID = None,
        depreciation_expense_account_id: uuid.UUID = None,
        last_depreciation_date: date | None = None,
        last_depreciation_period_id: uuid.UUID | None = None,
        created_at: datetime = None,
        updated_at: datetime | None = None,
        **kwargs,
    ):
        self.asset_id = asset_id or uuid.uuid4()
        self.lease_id = lease_id or uuid.uuid4()
        self.initial_measurement_date = initial_measurement_date or date(2024, 1, 1)
        self.lease_liability_at_commencement = lease_liability_at_commencement
        self.lease_payments_at_commencement = lease_payments_at_commencement
        self.initial_direct_costs = initial_direct_costs
        self.restoration_obligation = restoration_obligation
        self.lease_incentives_deducted = lease_incentives_deducted
        self.initial_rou_asset_value = initial_rou_asset_value
        self.depreciation_method = depreciation_method
        self.useful_life_months = useful_life_months
        self.residual_value = residual_value
        self.accumulated_depreciation = accumulated_depreciation
        self.impairment_losses = impairment_losses
        self.revaluation_adjustments = revaluation_adjustments
        self.modification_adjustments = modification_adjustments
        self.carrying_amount = carrying_amount
        self.rou_asset_account_id = rou_asset_account_id or uuid.uuid4()
        self.accumulated_depreciation_account_id = (
            accumulated_depreciation_account_id or uuid.uuid4()
        )
        self.depreciation_expense_account_id = (
            depreciation_expense_account_id or uuid.uuid4()
        )
        self.last_depreciation_date = last_depreciation_date
        self.last_depreciation_period_id = last_depreciation_period_id
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockLeaseModification:
    """Mock LeaseModification model."""

    def __init__(
        self,
        modification_id: uuid.UUID = None,
        lease_id: uuid.UUID = None,
        fiscal_period_id: uuid.UUID = None,
        modification_date: date = None,
        effective_date: date = None,
        modification_type: ModificationType = ModificationType.PAYMENT_CHANGE,
        description: str | None = None,
        is_separate_lease: bool = False,
        liability_before: Decimal = Decimal("200000.00"),
        rou_asset_before: Decimal = Decimal("200000.00"),
        remaining_lease_term_before: int = 48,
        discount_rate_before: Decimal = Decimal("0.05"),
        new_lease_payments: Decimal | None = None,
        revised_discount_rate: Decimal | None = None,
        revised_lease_term_months: int | None = None,
        liability_after: Decimal = Decimal("210000.00"),
        rou_asset_after: Decimal = Decimal("210000.00"),
        liability_adjustment: Decimal = Decimal("10000.00"),
        rou_asset_adjustment: Decimal = Decimal("10000.00"),
        gain_loss_on_modification: Decimal = Decimal("0"),
        journal_entry_id: uuid.UUID | None = None,
        created_by_user_id: uuid.UUID = None,
        approved_by_user_id: uuid.UUID | None = None,
        approved_at: datetime | None = None,
        created_at: datetime = None,
        **kwargs,
    ):
        self.modification_id = modification_id or uuid.uuid4()
        self.lease_id = lease_id or uuid.uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.modification_date = modification_date or date.today()
        self.effective_date = effective_date or date.today()
        self.modification_type = modification_type
        self.description = description
        self.is_separate_lease = is_separate_lease
        self.liability_before = liability_before
        self.rou_asset_before = rou_asset_before
        self.remaining_lease_term_before = remaining_lease_term_before
        self.discount_rate_before = discount_rate_before
        self.new_lease_payments = new_lease_payments
        self.revised_discount_rate = revised_discount_rate
        self.revised_lease_term_months = revised_lease_term_months
        self.liability_after = liability_after
        self.rou_asset_after = rou_asset_after
        self.liability_adjustment = liability_adjustment
        self.rou_asset_adjustment = rou_asset_adjustment
        self.gain_loss_on_modification = gain_loss_on_modification
        self.journal_entry_id = journal_entry_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at
        self.created_at = created_at or datetime.now(UTC)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockLeasePaymentSchedule:
    """Mock LeasePaymentSchedule model."""

    def __init__(
        self,
        schedule_id: uuid.UUID = None,
        lease_id: uuid.UUID = None,
        liability_id: uuid.UUID = None,
        payment_number: int = 1,
        payment_date: date = None,
        fiscal_period_id: uuid.UUID | None = None,
        total_payment: Decimal = Decimal("5000.00"),
        principal_portion: Decimal = Decimal("4000.00"),
        interest_portion: Decimal = Decimal("1000.00"),
        variable_payment: Decimal = Decimal("0"),
        opening_liability_balance: Decimal = Decimal("250000.00"),
        closing_liability_balance: Decimal = Decimal("246000.00"),
        is_index_adjusted: bool = False,
        index_adjustment_amount: Decimal = Decimal("0"),
        status: PaymentStatus = PaymentStatus.SCHEDULED,
        actual_payment_date: date | None = None,
        actual_payment_amount: Decimal | None = None,
        payment_reference: uuid.UUID | None = None,
        invoice_reference: uuid.UUID | None = None,
        interest_journal_entry_id: uuid.UUID | None = None,
        payment_journal_entry_id: uuid.UUID | None = None,
        created_at: datetime = None,
        updated_at: datetime | None = None,
        **kwargs,
    ):
        self.schedule_id = schedule_id or uuid.uuid4()
        self.lease_id = lease_id or uuid.uuid4()
        self.liability_id = liability_id or uuid.uuid4()
        self.payment_number = payment_number
        self.payment_date = payment_date or date(2024, 1, 1)
        self.fiscal_period_id = fiscal_period_id
        self.total_payment = total_payment
        self.principal_portion = principal_portion
        self.interest_portion = interest_portion
        self.variable_payment = variable_payment
        self.opening_liability_balance = opening_liability_balance
        self.closing_liability_balance = closing_liability_balance
        self.is_index_adjusted = is_index_adjusted
        self.index_adjustment_amount = index_adjustment_amount
        self.status = status
        self.actual_payment_date = actual_payment_date
        self.actual_payment_amount = actual_payment_amount
        self.payment_reference = payment_reference
        self.invoice_reference = invoice_reference
        self.interest_journal_entry_id = interest_journal_entry_id
        self.payment_journal_entry_id = payment_journal_entry_id
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    db.scalars.return_value.first.return_value = None
    db.scalars.return_value.all.return_value = []
    return db


@pytest.fixture
def org_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def approver_id() -> uuid.UUID:
    """Generate a test approver ID (different from creator)."""
    return uuid.uuid4()


@pytest.fixture
def mock_contract(org_id, user_id) -> MockLeaseContract:
    """Create a mock lease contract."""
    return MockLeaseContract(
        organization_id=org_id,
        created_by_user_id=user_id,
    )


@pytest.fixture
def mock_active_contract(org_id, user_id, approver_id) -> MockLeaseContract:
    """Create a mock active lease contract."""
    return MockLeaseContract(
        organization_id=org_id,
        created_by_user_id=user_id,
        approved_by_user_id=approver_id,
        approved_at=datetime.now(UTC),
        status=LeaseStatus.ACTIVE,
    )


@pytest.fixture
def mock_liability(mock_contract) -> MockLeaseLiability:
    """Create a mock lease liability."""
    return MockLeaseLiability(
        lease_id=mock_contract.lease_id,
    )


@pytest.fixture
def mock_asset(mock_contract) -> MockLeaseAsset:
    """Create a mock lease asset (ROU asset)."""
    return MockLeaseAsset(
        lease_id=mock_contract.lease_id,
    )


@pytest.fixture
def mock_modification(mock_contract, user_id) -> MockLeaseModification:
    """Create a mock lease modification."""
    return MockLeaseModification(
        lease_id=mock_contract.lease_id,
        created_by_user_id=user_id,
    )


@pytest.fixture
def mock_payment_schedule(mock_contract, mock_liability) -> MockLeasePaymentSchedule:
    """Create a mock payment schedule."""
    return MockLeasePaymentSchedule(
        lease_id=mock_contract.lease_id,
        liability_id=mock_liability.liability_id,
    )
