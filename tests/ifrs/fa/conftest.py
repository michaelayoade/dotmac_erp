"""
Fixtures for Fixed Assets (FA) Module Tests.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock

import pytest


# ============ Import Actual Enums ============
from app.models.finance.fa.asset import AssetStatus as MockAssetStatus
from app.models.finance.fa.asset_category import DepreciationMethod as MockDepreciationMethod


# ============ Mock Models ============


class MockAssetCategory:
    """Mock AssetCategory model."""

    def __init__(
        self,
        category_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        category_code: str = "EQUIPMENT",
        category_name: str = "Office Equipment",
        description: Optional[str] = None,
        parent_category_id: Optional[uuid.UUID] = None,
        depreciation_method: MockDepreciationMethod = MockDepreciationMethod.STRAIGHT_LINE,
        useful_life_months: int = 60,
        residual_value_percent: Decimal = Decimal("0"),
        asset_account_id: uuid.UUID = None,
        accumulated_depreciation_account_id: uuid.UUID = None,
        depreciation_expense_account_id: uuid.UUID = None,
        gain_loss_disposal_account_id: uuid.UUID = None,
        revaluation_surplus_account_id: Optional[uuid.UUID] = None,
        impairment_loss_account_id: Optional[uuid.UUID] = None,
        capitalization_threshold: Decimal = Decimal("1000"),
        revaluation_model_allowed: bool = False,
        is_active: bool = True,
        created_at: datetime = None,
        updated_at: Optional[datetime] = None,
        **kwargs
    ):
        self.category_id = category_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.category_code = category_code
        self.category_name = category_name
        self.description = description
        self.parent_category_id = parent_category_id
        self.depreciation_method = depreciation_method
        self.useful_life_months = useful_life_months
        self.residual_value_percent = residual_value_percent
        self.asset_account_id = asset_account_id or uuid.uuid4()
        self.accumulated_depreciation_account_id = accumulated_depreciation_account_id or uuid.uuid4()
        self.depreciation_expense_account_id = depreciation_expense_account_id or uuid.uuid4()
        self.gain_loss_disposal_account_id = gain_loss_disposal_account_id or uuid.uuid4()
        self.revaluation_surplus_account_id = revaluation_surplus_account_id or uuid.uuid4()
        self.impairment_loss_account_id = impairment_loss_account_id
        self.capitalization_threshold = capitalization_threshold
        self.revaluation_model_allowed = revaluation_model_allowed
        self.is_active = is_active
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockAsset:
    """Mock Asset model."""

    def __init__(
        self,
        asset_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        asset_number: str = "FA-0001",
        asset_name: str = "Office Computer",
        description: Optional[str] = None,
        category_id: uuid.UUID = None,
        location_id: Optional[uuid.UUID] = None,
        cost_center_id: Optional[uuid.UUID] = None,
        custodian_user_id: Optional[uuid.UUID] = None,
        acquisition_date: date = None,
        in_service_date: Optional[date] = None,
        acquisition_cost: Decimal = Decimal("5000.00"),
        currency_code: str = "USD",
        functional_currency_cost: Decimal = None,
        source_type: Optional[str] = None,
        source_document_id: Optional[uuid.UUID] = None,
        supplier_id: Optional[uuid.UUID] = None,
        invoice_reference: Optional[str] = None,
        depreciation_method: str = MockDepreciationMethod.STRAIGHT_LINE.value,
        useful_life_months: int = 60,
        remaining_life_months: int = 60,
        residual_value: Decimal = Decimal("0"),
        depreciation_start_date: Optional[date] = None,
        accumulated_depreciation: Decimal = Decimal("0"),
        net_book_value: Decimal = None,
        revalued_amount: Optional[Decimal] = None,
        impairment_loss: Decimal = Decimal("0"),
        status: MockAssetStatus = MockAssetStatus.DRAFT,
        cash_generating_unit_id: Optional[uuid.UUID] = None,
        serial_number: Optional[str] = None,
        barcode: Optional[str] = None,
        manufacturer: Optional[str] = None,
        model: Optional[str] = None,
        warranty_expiry_date: Optional[date] = None,
        insured_value: Optional[Decimal] = None,
        insurance_policy_number: Optional[str] = None,
        disposal_date: Optional[date] = None,
        disposal_proceeds: Optional[Decimal] = None,
        disposal_gain_loss: Optional[Decimal] = None,
        is_component_parent: bool = False,
        parent_asset_id: Optional[uuid.UUID] = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        updated_at: Optional[datetime] = None,
        **kwargs
    ):
        self.asset_id = asset_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.asset_number = asset_number
        self.asset_name = asset_name
        self.description = description
        self.category_id = category_id or uuid.uuid4()
        self.location_id = location_id
        self.cost_center_id = cost_center_id
        self.custodian_user_id = custodian_user_id
        self.acquisition_date = acquisition_date or date.today()
        self.in_service_date = in_service_date
        self.acquisition_cost = acquisition_cost
        self.currency_code = currency_code
        self.functional_currency_cost = functional_currency_cost or acquisition_cost
        self.source_type = source_type
        self.source_document_id = source_document_id
        self.supplier_id = supplier_id
        self.invoice_reference = invoice_reference
        self.depreciation_method = depreciation_method
        self.useful_life_months = useful_life_months
        self.remaining_life_months = remaining_life_months
        self.residual_value = residual_value
        self.depreciation_start_date = depreciation_start_date
        self.accumulated_depreciation = accumulated_depreciation
        self.net_book_value = net_book_value or (acquisition_cost - accumulated_depreciation)
        self.revalued_amount = revalued_amount
        self.impairment_loss = impairment_loss
        self.status = status
        self.cash_generating_unit_id = cash_generating_unit_id
        self.serial_number = serial_number
        self.barcode = barcode
        self.manufacturer = manufacturer
        self.model = model
        self.warranty_expiry_date = warranty_expiry_date
        self.insured_value = insured_value
        self.insurance_policy_number = insurance_policy_number
        self.disposal_date = disposal_date
        self.disposal_proceeds = disposal_proceeds
        self.disposal_gain_loss = disposal_gain_loss
        self.is_component_parent = is_component_parent
        self.parent_asset_id = parent_asset_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockDepreciationRun:
    """Mock DepreciationRun model."""

    def __init__(
        self,
        run_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        run_number: str = "DEP-2024-01",
        fiscal_period_id: uuid.UUID = None,
        run_date: date = None,
        description: Optional[str] = None,
        status: str = "DRAFT",
        total_depreciation: Decimal = Decimal("0"),
        asset_count: int = 0,
        posted_journal_id: Optional[uuid.UUID] = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        **kwargs
    ):
        self.run_id = run_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.run_number = run_number
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.run_date = run_date or date.today()
        self.description = description
        self.status = status
        self.total_depreciation = total_depreciation
        self.asset_count = asset_count
        self.posted_journal_id = posted_journal_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockDepreciationSchedule:
    """Mock DepreciationSchedule model."""

    def __init__(
        self,
        schedule_id: uuid.UUID = None,
        run_id: uuid.UUID = None,
        asset_id: uuid.UUID = None,
        period_number: int = 1,
        depreciation_amount: Decimal = Decimal("100.00"),
        opening_nbv: Decimal = Decimal("5000.00"),
        closing_nbv: Decimal = Decimal("4900.00"),
        depreciation_method: str = MockDepreciationMethod.STRAIGHT_LINE.value,
        expense_account_id: uuid.UUID = None,
        accumulated_depreciation_account_id: uuid.UUID = None,
        **kwargs
    ):
        self.schedule_id = schedule_id or uuid.uuid4()
        self.run_id = run_id or uuid.uuid4()
        self.asset_id = asset_id or uuid.uuid4()
        self.period_number = period_number
        self.depreciation_amount = depreciation_amount
        self.opening_nbv = opening_nbv
        self.closing_nbv = closing_nbv
        self.depreciation_method = depreciation_method
        self.expense_account_id = expense_account_id or uuid.uuid4()
        self.accumulated_depreciation_account_id = accumulated_depreciation_account_id or uuid.uuid4()
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockAssetDisposal:
    """Mock AssetDisposal model."""

    def __init__(
        self,
        disposal_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        asset_id: uuid.UUID = None,
        disposal_date: date = None,
        disposal_type: str = "SALE",
        disposal_proceeds: Decimal = Decimal("1000.00"),
        costs_of_disposal: Decimal = Decimal("0"),
        net_proceeds: Decimal = Decimal("1000.00"),
        net_book_value_at_disposal: Decimal = Decimal("800.00"),
        gain_loss_on_disposal: Decimal = Decimal("200.00"),
        reason: Optional[str] = None,
        buyer_name: Optional[str] = None,
        status: str = "DRAFT",
        posted_journal_id: Optional[uuid.UUID] = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        **kwargs
    ):
        self.disposal_id = disposal_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.asset_id = asset_id or uuid.uuid4()
        self.disposal_date = disposal_date or date.today()
        self.disposal_type = disposal_type
        self.disposal_proceeds = disposal_proceeds
        self.costs_of_disposal = costs_of_disposal
        self.net_proceeds = net_proceeds
        self.net_book_value_at_disposal = net_book_value_at_disposal
        self.gain_loss_on_disposal = gain_loss_on_disposal
        self.reason = reason
        self.buyer_name = buyer_name
        self.status = status
        self.posted_journal_id = posted_journal_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockAssetRevaluation:
    """Mock AssetRevaluation model."""

    def __init__(
        self,
        revaluation_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        asset_id: uuid.UUID = None,
        revaluation_date: date = None,
        carrying_amount_before: Decimal = Decimal("5000.00"),
        fair_value: Decimal = Decimal("6000.00"),
        revaluation_surplus_or_deficit: Decimal = Decimal("1000.00"),
        surplus_to_equity: Decimal = Decimal("1000.00"),
        deficit_to_pl: Decimal = Decimal("0"),
        prior_deficit_reversed: Decimal = Decimal("0"),
        prior_surplus_reversed: Decimal = Decimal("0"),
        appraiser_name: Optional[str] = None,
        valuation_method: Optional[str] = None,
        status: str = "DRAFT",
        posted_journal_id: Optional[uuid.UUID] = None,
        created_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
        **kwargs
    ):
        self.revaluation_id = revaluation_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.asset_id = asset_id or uuid.uuid4()
        self.revaluation_date = revaluation_date or date.today()
        self.carrying_amount_before = carrying_amount_before
        self.fair_value = fair_value
        self.revaluation_surplus_or_deficit = revaluation_surplus_or_deficit
        self.surplus_to_equity = surplus_to_equity
        self.deficit_to_pl = deficit_to_pl
        self.prior_deficit_reversed = prior_deficit_reversed
        self.prior_surplus_reversed = prior_surplus_reversed
        self.appraiser_name = appraiser_name
        self.valuation_method = valuation_method
        self.status = status
        self.posted_journal_id = posted_journal_id
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
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
def mock_category(org_id) -> MockAssetCategory:
    """Create a mock asset category."""
    return MockAssetCategory(organization_id=org_id)


@pytest.fixture
def mock_asset(org_id, mock_category, user_id) -> MockAsset:
    """Create a mock asset."""
    return MockAsset(
        organization_id=org_id,
        category_id=mock_category.category_id,
        created_by_user_id=user_id,
    )


@pytest.fixture
def mock_depreciation_run(org_id, user_id) -> MockDepreciationRun:
    """Create a mock depreciation run."""
    return MockDepreciationRun(
        organization_id=org_id,
        created_by_user_id=user_id,
    )


@pytest.fixture
def mock_disposal(org_id, mock_asset, user_id) -> MockAssetDisposal:
    """Create a mock asset disposal."""
    return MockAssetDisposal(
        organization_id=org_id,
        asset_id=mock_asset.asset_id,
        created_by_user_id=user_id,
    )


@pytest.fixture
def mock_revaluation(org_id, mock_asset, user_id) -> MockAssetRevaluation:
    """Create a mock asset revaluation."""
    return MockAssetRevaluation(
        organization_id=org_id,
        asset_id=mock_asset.asset_id,
        created_by_user_id=user_id,
    )
