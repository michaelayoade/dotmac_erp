"""
Fixtures for AP Services Tests.

These tests use mock objects to avoid PostgreSQL-specific dependencies
while still testing the service logic.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest


# ============ Mock Enums ============

from app.models.ifrs.ap.supplier import SupplierType
from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceStatus, SupplierInvoiceType
from app.models.ifrs.ap.supplier_payment import APPaymentStatus, APPaymentMethod

MockSupplierType = SupplierType
MockSupplierInvoiceStatus = SupplierInvoiceStatus
MockSupplierInvoiceType = SupplierInvoiceType
MockAPPaymentStatus = APPaymentStatus
MockAPPaymentMethod = APPaymentMethod


# ============ Mock Model Classes ============


class MockSupplier:
    """Mock Supplier model."""

    def __init__(
        self,
        supplier_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        supplier_code: str = "SUP001",
        supplier_type: SupplierType = SupplierType.VENDOR,
        legal_name: str = "Test Supplier",
        trading_name: Optional[str] = None,
        tax_identification_number: Optional[str] = None,
        registration_number: Optional[str] = None,
        payment_terms_days: int = 30,
        currency_code: str = "USD",
        default_expense_account_id: Optional[uuid.UUID] = None,
        ap_control_account_id: Optional[uuid.UUID] = None,
        supplier_group_id: Optional[uuid.UUID] = None,
        is_related_party: bool = False,
        related_party_relationship: Optional[str] = None,
        withholding_tax_applicable: bool = False,
        withholding_tax_code_id: Optional[uuid.UUID] = None,
        billing_address: Optional[dict] = None,
        remittance_address: Optional[dict] = None,
        primary_contact: Optional[dict] = None,
        bank_details: Optional[dict] = None,
        is_active: bool = True,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.supplier_id = supplier_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.supplier_code = supplier_code
        self.supplier_type = supplier_type
        self.legal_name = legal_name
        self.trading_name = trading_name
        self.tax_identification_number = tax_identification_number
        self.registration_number = registration_number
        self.payment_terms_days = payment_terms_days
        self.currency_code = currency_code
        self.default_expense_account_id = default_expense_account_id
        self.ap_control_account_id = ap_control_account_id or uuid.uuid4()
        self.supplier_group_id = supplier_group_id
        self.is_related_party = is_related_party
        self.related_party_relationship = related_party_relationship
        self.withholding_tax_applicable = withholding_tax_applicable
        self.withholding_tax_code_id = withholding_tax_code_id
        self.billing_address = billing_address
        self.remittance_address = remittance_address
        self.primary_contact = primary_contact
        self.bank_details = bank_details
        self.is_active = is_active
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at


class MockSupplierInvoice:
    """Mock SupplierInvoice model."""

    def __init__(
        self,
        invoice_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        supplier_id: uuid.UUID = None,
        invoice_number: str = "APINV-0001",
        supplier_invoice_number: Optional[str] = None,
        invoice_type: SupplierInvoiceType = SupplierInvoiceType.STANDARD,
        invoice_date: date = None,
        received_date: date = None,
        due_date: date = None,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        subtotal: Decimal = Decimal("1000.00"),
        tax_amount: Decimal = Decimal("0"),
        total_amount: Decimal = Decimal("1000.00"),
        amount_paid: Decimal = Decimal("0"),
        functional_currency_amount: Decimal = Decimal("1000.00"),
        status: SupplierInvoiceStatus = SupplierInvoiceStatus.DRAFT,
        ap_control_account_id: Optional[uuid.UUID] = None,
        journal_entry_id: Optional[uuid.UUID] = None,
        posting_batch_id: Optional[uuid.UUID] = None,
        posting_status: str = "NOT_POSTED",
        created_by_user_id: Optional[uuid.UUID] = None,
        submitted_by_user_id: Optional[uuid.UUID] = None,
        submitted_at: Optional[datetime] = None,
        approved_by_user_id: Optional[uuid.UUID] = None,
        approved_at: Optional[datetime] = None,
        posted_by_user_id: Optional[uuid.UUID] = None,
        posted_at: Optional[datetime] = None,
        created_at: datetime = None,
    ):
        self.invoice_id = invoice_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.supplier_id = supplier_id or uuid.uuid4()
        self.invoice_number = invoice_number
        self.supplier_invoice_number = supplier_invoice_number
        self.invoice_type = invoice_type
        self.invoice_date = invoice_date or date.today()
        self.received_date = received_date or date.today()
        self.due_date = due_date or (date.today() + timedelta(days=30))
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.subtotal = subtotal
        self.tax_amount = tax_amount
        self.total_amount = total_amount
        self.amount_paid = amount_paid
        self.functional_currency_amount = functional_currency_amount
        self.status = status
        self.ap_control_account_id = ap_control_account_id or uuid.uuid4()
        self.journal_entry_id = journal_entry_id
        self.posting_batch_id = posting_batch_id
        self.posting_status = posting_status
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.submitted_by_user_id = submitted_by_user_id
        self.submitted_at = submitted_at
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at
        self.posted_by_user_id = posted_by_user_id
        self.posted_at = posted_at
        self.created_at = created_at or datetime.now(timezone.utc)
        self.lines = []

    @property
    def balance_due(self) -> Decimal:
        return self.total_amount - self.amount_paid


class MockSupplierInvoiceLine:
    """Mock SupplierInvoiceLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        invoice_id: uuid.UUID = None,
        line_number: int = 1,
        account_id: Optional[uuid.UUID] = None,
        description: str = "Test line",
        quantity: Decimal = Decimal("1"),
        unit_price: Decimal = Decimal("1000.00"),
        amount: Decimal = Decimal("1000.00"),
        tax_code_id: Optional[uuid.UUID] = None,
        tax_amount: Decimal = Decimal("0"),
    ):
        self.line_id = line_id or uuid.uuid4()
        self.invoice_id = invoice_id or uuid.uuid4()
        self.line_number = line_number
        self.account_id = account_id or uuid.uuid4()
        self.description = description
        self.quantity = quantity
        self.unit_price = unit_price
        self.amount = amount
        self.tax_code_id = tax_code_id
        self.tax_amount = tax_amount


class MockSupplierPayment:
    """Mock SupplierPayment model."""

    def __init__(
        self,
        payment_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        supplier_id: uuid.UUID = None,
        payment_number: str = "PAY-0001",
        payment_date: date = None,
        payment_method: APPaymentMethod = APPaymentMethod.CHECK,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        amount: Decimal = Decimal("1000.00"),
        functional_currency_amount: Decimal = Decimal("1000.00"),
        status: APPaymentStatus = APPaymentStatus.DRAFT,
        bank_account_id: Optional[uuid.UUID] = None,
        reference: Optional[str] = None,
        created_by_user_id: Optional[uuid.UUID] = None,
        approved_by_user_id: Optional[uuid.UUID] = None,
        created_at: datetime = None,
    ):
        self.payment_id = payment_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.supplier_id = supplier_id or uuid.uuid4()
        self.payment_number = payment_number
        self.payment_date = payment_date or date.today()
        self.payment_method = payment_method
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.amount = amount
        self.payment_amount = amount  # Alias for posting adapter
        self.functional_currency_amount = functional_currency_amount
        self.status = status
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.reference = reference
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.approved_by_user_id = approved_by_user_id
        self.created_at = created_at or datetime.now(timezone.utc)
        self.correlation_id = uuid.uuid4()
        self.allocations = []


class MockAPPaymentAllocation:
    """Mock APPaymentAllocation model."""

    def __init__(
        self,
        allocation_id: uuid.UUID = None,
        payment_id: uuid.UUID = None,
        invoice_id: uuid.UUID = None,
        allocated_amount: Decimal = Decimal("1000.00"),
        allocated_at: datetime = None,
    ):
        self.allocation_id = allocation_id or uuid.uuid4()
        self.payment_id = payment_id or uuid.uuid4()
        self.invoice_id = invoice_id or uuid.uuid4()
        self.allocated_amount = allocated_amount
        self.allocated_at = allocated_at or datetime.now(timezone.utc)


class MockAPAgingSnapshot:
    """Mock APAgingSnapshot model."""

    def __init__(
        self,
        snapshot_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        snapshot_date: date = None,
        supplier_id: Optional[uuid.UUID] = None,
        current_amount: Decimal = Decimal("0"),
        days_1_30: Decimal = Decimal("0"),
        days_31_60: Decimal = Decimal("0"),
        days_61_90: Decimal = Decimal("0"),
        over_90_days: Decimal = Decimal("0"),
        total_outstanding: Decimal = Decimal("0"),
    ):
        self.snapshot_id = snapshot_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.snapshot_date = snapshot_date or date.today()
        self.supplier_id = supplier_id
        self.current_amount = current_amount
        self.days_1_30 = days_1_30
        self.days_31_60 = days_31_60
        self.days_61_90 = days_61_90
        self.over_90_days = over_90_days
        self.total_outstanding = total_outstanding


# ============ Fixtures ============


@pytest.fixture
def organization_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    session.execute = MagicMock()
    return session


@pytest.fixture
def mock_supplier(organization_id) -> MockSupplier:
    """Create a mock supplier."""
    return MockSupplier(organization_id=organization_id)


@pytest.fixture
def mock_supplier_invoice(organization_id, mock_supplier) -> MockSupplierInvoice:
    """Create a mock supplier invoice."""
    return MockSupplierInvoice(
        organization_id=organization_id,
        supplier_id=mock_supplier.supplier_id,
    )


@pytest.fixture
def mock_supplier_payment(organization_id, mock_supplier) -> MockSupplierPayment:
    """Create a mock supplier payment."""
    return MockSupplierPayment(
        organization_id=organization_id,
        supplier_id=mock_supplier.supplier_id,
    )
