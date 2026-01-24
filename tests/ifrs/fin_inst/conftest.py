"""
Fixtures for Financial Instruments module tests.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock

import pytest

# Import actual enums from models
from app.models.finance.fin_inst.financial_instrument import (
    InstrumentType,
    InstrumentClassification,
    InstrumentStatus,
)
from app.models.finance.fin_inst.hedge_relationship import HedgeType, HedgeStatus


# ============ Mock Models ============


class MockFinancialInstrument:
    """Mock FinancialInstrument model."""

    def __init__(
        self,
        instrument_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        instrument_code: str = "BOND-001",
        instrument_name: str = "Corporate Bond",
        description: Optional[str] = None,
        instrument_type: InstrumentType = InstrumentType.DEBT_SECURITY,
        classification: InstrumentClassification = InstrumentClassification.AMORTIZED_COST,
        is_asset: bool = True,
        counterparty_type: str = "CORPORATE",
        counterparty_id: Optional[uuid.UUID] = None,
        counterparty_name: str = "ABC Corporation",
        isin: Optional[str] = None,
        cusip: Optional[str] = None,
        external_reference: Optional[str] = None,
        currency_code: str = "USD",
        face_value: Decimal = Decimal("100000.00"),
        current_principal: Decimal = Decimal("100000.00"),
        trade_date: date = None,
        settlement_date: date = None,
        maturity_date: Optional[date] = None,
        is_interest_bearing: bool = True,
        interest_rate_type: Optional[str] = "FIXED",
        stated_interest_rate: Optional[Decimal] = Decimal("0.05"),
        effective_interest_rate: Optional[Decimal] = Decimal("0.055"),
        interest_payment_frequency: Optional[str] = "SEMI_ANNUAL",
        day_count_convention: Optional[str] = "30/360",
        next_interest_date: Optional[date] = None,
        acquisition_cost: Decimal = Decimal("98000.00"),
        transaction_costs: Decimal = Decimal("500.00"),
        premium_discount: Decimal = Decimal("1500.00"),
        amortized_cost: Decimal = Decimal("98500.00"),
        fair_value: Optional[Decimal] = Decimal("99000.00"),
        carrying_amount: Decimal = Decimal("98500.00"),
        ecl_stage: int = 1,
        loss_allowance: Decimal = Decimal("0"),
        is_credit_impaired: bool = False,
        accumulated_oci: Decimal = Decimal("0"),
        status: InstrumentStatus = InstrumentStatus.ACTIVE,
        instrument_account_id: uuid.UUID = None,
        interest_receivable_account_id: Optional[uuid.UUID] = None,
        interest_income_account_id: Optional[uuid.UUID] = None,
        fv_adjustment_account_id: Optional[uuid.UUID] = None,
        oci_account_id: Optional[uuid.UUID] = None,
        ecl_expense_account_id: Optional[uuid.UUID] = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        updated_at: Optional[datetime] = None,
        **kwargs
    ):
        self.instrument_id = instrument_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.instrument_code = instrument_code
        self.instrument_name = instrument_name
        self.description = description
        self.instrument_type = instrument_type
        self.classification = classification
        self.is_asset = is_asset
        self.counterparty_type = counterparty_type
        self.counterparty_id = counterparty_id
        self.counterparty_name = counterparty_name
        self.isin = isin
        self.cusip = cusip
        self.external_reference = external_reference
        self.currency_code = currency_code
        self.face_value = face_value
        self.current_principal = current_principal
        self.trade_date = trade_date or date(2024, 1, 1)
        self.settlement_date = settlement_date or date(2024, 1, 3)
        self.maturity_date = maturity_date or date(2029, 1, 1)
        self.is_interest_bearing = is_interest_bearing
        self.interest_rate_type = interest_rate_type
        self.stated_interest_rate = stated_interest_rate
        self.effective_interest_rate = effective_interest_rate
        self.interest_payment_frequency = interest_payment_frequency
        self.day_count_convention = day_count_convention
        self.next_interest_date = next_interest_date
        self.acquisition_cost = acquisition_cost
        self.transaction_costs = transaction_costs
        self.premium_discount = premium_discount
        self.amortized_cost = amortized_cost
        self.fair_value = fair_value
        self.carrying_amount = carrying_amount
        self.ecl_stage = ecl_stage
        self.loss_allowance = loss_allowance
        self.is_credit_impaired = is_credit_impaired
        self.accumulated_oci = accumulated_oci
        self.status = status
        self.instrument_account_id = instrument_account_id or uuid.uuid4()
        self.interest_receivable_account_id = interest_receivable_account_id
        self.interest_income_account_id = interest_income_account_id
        self.fv_adjustment_account_id = fv_adjustment_account_id
        self.oci_account_id = oci_account_id
        self.ecl_expense_account_id = ecl_expense_account_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockHedgeRelationship:
    """Mock HedgeRelationship model."""

    def __init__(
        self,
        hedge_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        hedge_code: str = "HEDGE-001",
        hedge_name: str = "FX Cash Flow Hedge",
        description: Optional[str] = None,
        hedge_type: HedgeType = HedgeType.CASH_FLOW,
        hedging_instrument_id: uuid.UUID = None,
        hedging_instrument_proportion: Decimal = Decimal("1.0"),
        hedged_item_type: str = "FORECAST_TRANSACTION",
        hedged_item_id: Optional[uuid.UUID] = None,
        hedged_item_description: str = "Forecasted USD revenue",
        hedged_risk: str = "FX_RISK",
        hedge_ratio: Decimal = Decimal("1.0"),
        designation_date: date = None,
        effective_date: date = None,
        termination_date: Optional[date] = None,
        status: HedgeStatus = HedgeStatus.ACTIVE,
        prospective_test_method: str = "CRITICAL_TERMS",
        prospective_test_passed: bool = True,
        retrospective_test_method: str = "DOLLAR_OFFSET",
        cash_flow_hedge_reserve: Decimal = Decimal("0"),
        cost_of_hedging_reserve: Decimal = Decimal("0"),
        documentation_reference: Optional[str] = None,
        created_by_user_id: uuid.UUID = None,
        approved_by_user_id: Optional[uuid.UUID] = None,
        approved_at: Optional[datetime] = None,
        created_at: datetime = None,
        updated_at: Optional[datetime] = None,
        **kwargs
    ):
        self.hedge_id = hedge_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.hedge_code = hedge_code
        self.hedge_name = hedge_name
        self.description = description
        self.hedge_type = hedge_type
        self.hedging_instrument_id = hedging_instrument_id or uuid.uuid4()
        self.hedging_instrument_proportion = hedging_instrument_proportion
        self.hedged_item_type = hedged_item_type
        self.hedged_item_id = hedged_item_id
        self.hedged_item_description = hedged_item_description
        self.hedged_risk = hedged_risk
        self.hedge_ratio = hedge_ratio
        self.designation_date = designation_date or date.today()
        self.effective_date = effective_date or date.today()
        self.termination_date = termination_date
        self.status = status
        self.prospective_test_method = prospective_test_method
        self.prospective_test_passed = prospective_test_passed
        self.retrospective_test_method = retrospective_test_method
        self.cash_flow_hedge_reserve = cash_flow_hedge_reserve
        self.cost_of_hedging_reserve = cost_of_hedging_reserve
        self.documentation_reference = documentation_reference
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockHedgeEffectiveness:
    """Mock HedgeEffectiveness model."""

    def __init__(
        self,
        effectiveness_id: uuid.UUID = None,
        hedge_id: uuid.UUID = None,
        fiscal_period_id: uuid.UUID = None,
        test_date: date = None,
        prospective_test_passed: bool = True,
        prospective_test_result: Optional[Decimal] = None,
        prospective_test_notes: Optional[str] = None,
        hedging_instrument_fv_change: Decimal = Decimal("1000.00"),
        hedged_item_fv_change: Decimal = Decimal("-980.00"),
        hedge_effectiveness_ratio: Decimal = Decimal("1.02"),
        retrospective_test_passed: bool = True,
        hedge_ineffectiveness: Decimal = Decimal("20.00"),
        ineffectiveness_recognized_pl: Decimal = Decimal("20.00"),
        effective_portion: Decimal = Decimal("980.00"),
        effective_portion_oci: Decimal = Decimal("980.00"),
        reclassification_to_pl: Decimal = Decimal("0"),
        is_highly_effective: bool = True,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        **kwargs
    ):
        self.effectiveness_id = effectiveness_id or uuid.uuid4()
        self.hedge_id = hedge_id or uuid.uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.test_date = test_date or date.today()
        self.prospective_test_passed = prospective_test_passed
        self.prospective_test_result = prospective_test_result
        self.prospective_test_notes = prospective_test_notes
        self.hedging_instrument_fv_change = hedging_instrument_fv_change
        self.hedged_item_fv_change = hedged_item_fv_change
        self.hedge_effectiveness_ratio = hedge_effectiveness_ratio
        self.retrospective_test_passed = retrospective_test_passed
        self.hedge_ineffectiveness = hedge_ineffectiveness
        self.ineffectiveness_recognized_pl = ineffectiveness_recognized_pl
        self.effective_portion = effective_portion
        self.effective_portion_oci = effective_portion_oci
        self.reclassification_to_pl = reclassification_to_pl
        self.is_highly_effective = is_highly_effective
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockInterestAccrual:
    """Mock InterestAccrual model."""

    def __init__(
        self,
        accrual_id: uuid.UUID = None,
        instrument_id: uuid.UUID = None,
        fiscal_period_id: uuid.UUID = None,
        accrual_date: date = None,
        accrual_start_date: date = None,
        accrual_end_date: date = None,
        days_accrued: int = 30,
        principal_balance: Decimal = Decimal("100000.00"),
        interest_rate: Decimal = Decimal("0.05"),
        accrued_interest: Decimal = Decimal("416.67"),
        amortization_amount: Decimal = Decimal("0"),
        is_posted: bool = False,
        journal_entry_id: Optional[uuid.UUID] = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        **kwargs
    ):
        self.accrual_id = accrual_id or uuid.uuid4()
        self.instrument_id = instrument_id or uuid.uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.accrual_date = accrual_date or date.today()
        self.accrual_start_date = accrual_start_date or date.today()
        self.accrual_end_date = accrual_end_date or date.today()
        self.days_accrued = days_accrued
        self.principal_balance = principal_balance
        self.interest_rate = interest_rate
        self.accrued_interest = accrued_interest
        self.amortization_amount = amortization_amount
        self.is_posted = is_posted
        self.journal_entry_id = journal_entry_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockInstrumentValuation:
    """Mock InstrumentValuation model."""

    def __init__(
        self,
        valuation_id: uuid.UUID = None,
        instrument_id: uuid.UUID = None,
        fiscal_period_id: uuid.UUID = None,
        valuation_date: date = None,
        valuation_method: str = "MARKET_PRICE",
        previous_fair_value: Decimal = Decimal("98000.00"),
        new_fair_value: Decimal = Decimal("99000.00"),
        fair_value_change: Decimal = Decimal("1000.00"),
        fv_change_to_pl: Decimal = Decimal("0"),
        fv_change_to_oci: Decimal = Decimal("1000.00"),
        source: str = "BLOOMBERG",
        is_posted: bool = False,
        journal_entry_id: Optional[uuid.UUID] = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        **kwargs
    ):
        self.valuation_id = valuation_id or uuid.uuid4()
        self.instrument_id = instrument_id or uuid.uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.valuation_date = valuation_date or date.today()
        self.valuation_method = valuation_method
        self.previous_fair_value = previous_fair_value
        self.new_fair_value = new_fair_value
        self.fair_value_change = fair_value_change
        self.fv_change_to_pl = fv_change_to_pl
        self.fv_change_to_oci = fv_change_to_oci
        self.source = source
        self.is_posted = is_posted
        self.journal_entry_id = journal_entry_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
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
def mock_instrument(org_id, user_id) -> MockFinancialInstrument:
    """Create a mock financial instrument."""
    return MockFinancialInstrument(
        organization_id=org_id,
        created_by_user_id=user_id,
    )


@pytest.fixture
def mock_fvpl_instrument(org_id, user_id) -> MockFinancialInstrument:
    """Create a mock FVPL instrument."""
    return MockFinancialInstrument(
        organization_id=org_id,
        created_by_user_id=user_id,
        classification=InstrumentClassification.FVPL,
        instrument_type=InstrumentType.EQUITY_SECURITY,
    )


@pytest.fixture
def mock_fvoci_instrument(org_id, user_id) -> MockFinancialInstrument:
    """Create a mock FVOCI debt instrument."""
    return MockFinancialInstrument(
        organization_id=org_id,
        created_by_user_id=user_id,
        classification=InstrumentClassification.FVOCI_DEBT,
    )


@pytest.fixture
def mock_hedge(org_id, user_id, mock_instrument) -> MockHedgeRelationship:
    """Create a mock hedge relationship."""
    return MockHedgeRelationship(
        organization_id=org_id,
        created_by_user_id=user_id,
        hedging_instrument_id=mock_instrument.instrument_id,
    )


@pytest.fixture
def mock_effectiveness(mock_hedge, user_id) -> MockHedgeEffectiveness:
    """Create a mock hedge effectiveness record."""
    return MockHedgeEffectiveness(
        hedge_id=mock_hedge.hedge_id,
        created_by_user_id=user_id,
    )
