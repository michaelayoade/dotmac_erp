"""
Fixtures for AR Services Tests.

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

from app.models.finance.ar.customer import CustomerType, RiskCategory
from app.models.finance.ar.customer_payment import PaymentMethod, PaymentStatus
from app.models.finance.ar.invoice import InvoiceStatus, InvoiceType

MockCustomerType = CustomerType
MockRiskCategory = RiskCategory
MockInvoiceStatus = InvoiceStatus
MockInvoiceType = InvoiceType
MockPaymentStatus = PaymentStatus
MockPaymentMethod = PaymentMethod


# ============ Mock Model Classes ============


class MockCustomer:
    """Mock Customer model."""

    def __init__(
        self,
        customer_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        customer_code: str = "CUS001",
        customer_type: CustomerType = CustomerType.COMPANY,
        legal_name: str = "Test Customer",
        trading_name: Optional[str] = None,
        tax_identification_number: Optional[str] = None,
        registration_number: Optional[str] = None,
        credit_limit: Optional[Decimal] = None,
        credit_terms_days: int = 30,
        payment_terms_id: Optional[uuid.UUID] = None,
        currency_code: str = "USD",
        price_list_id: Optional[uuid.UUID] = None,
        ar_control_account_id: Optional[uuid.UUID] = None,
        default_revenue_account_id: Optional[uuid.UUID] = None,
        sales_rep_user_id: Optional[uuid.UUID] = None,
        customer_group_id: Optional[uuid.UUID] = None,
        risk_category: Optional[RiskCategory] = None,
        is_related_party: bool = False,
        related_party_type: Optional[str] = None,
        related_party_relationship: Optional[str] = None,
        billing_address: Optional[dict] = None,
        shipping_address: Optional[dict] = None,
        primary_contact: Optional[dict] = None,
        bank_details: Optional[dict] = None,
        is_active: bool = True,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.customer_id = customer_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.customer_code = customer_code
        self.customer_type = customer_type
        self.legal_name = legal_name
        self.trading_name = trading_name
        self.tax_identification_number = tax_identification_number
        self.registration_number = registration_number
        self.credit_limit = credit_limit
        self.credit_terms_days = credit_terms_days
        self.payment_terms_id = payment_terms_id
        self.currency_code = currency_code
        self.price_list_id = price_list_id
        self.ar_control_account_id = ar_control_account_id or uuid.uuid4()
        self.default_revenue_account_id = default_revenue_account_id
        self.sales_rep_user_id = sales_rep_user_id
        self.customer_group_id = customer_group_id
        self.risk_category = risk_category or RiskCategory.MEDIUM
        self.is_related_party = is_related_party
        self.related_party_type = related_party_type
        self.related_party_relationship = related_party_relationship
        self.billing_address = billing_address
        self.shipping_address = shipping_address
        self.primary_contact = primary_contact
        self.bank_details = bank_details
        self.is_active = is_active
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at


class MockInvoice:
    """Mock Invoice model."""

    def __init__(
        self,
        invoice_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        customer_id: uuid.UUID = None,
        invoice_number: str = "INV-0001",
        invoice_type: InvoiceType = InvoiceType.STANDARD,
        invoice_date: date = None,
        due_date: date = None,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        subtotal: Decimal = Decimal("1000.00"),
        tax_amount: Decimal = Decimal("0"),
        total_amount: Decimal = Decimal("1000.00"),
        amount_paid: Decimal = Decimal("0"),
        functional_currency_amount: Decimal = Decimal("1000.00"),
        status: InvoiceStatus = InvoiceStatus.DRAFT,
        ar_control_account_id: Optional[uuid.UUID] = None,
        journal_entry_id: Optional[uuid.UUID] = None,
        posting_batch_id: Optional[uuid.UUID] = None,
        posting_status: str = "NOT_POSTED",
        contract_id: Optional[uuid.UUID] = None,
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
        self.customer_id = customer_id or uuid.uuid4()
        self.invoice_number = invoice_number
        self.invoice_type = invoice_type
        self.invoice_date = invoice_date or date.today()
        self.due_date = due_date or (date.today() + timedelta(days=30))
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.subtotal = subtotal
        self.tax_amount = tax_amount
        self.total_amount = total_amount
        self.amount_paid = amount_paid
        self.functional_currency_amount = functional_currency_amount
        self.status = status
        self.ar_control_account_id = ar_control_account_id or uuid.uuid4()
        self.journal_entry_id = journal_entry_id
        self.posting_batch_id = posting_batch_id
        self.posting_status = posting_status
        self.contract_id = contract_id
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


class MockInvoiceLine:
    """Mock InvoiceLine model."""

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
        performance_obligation_id: Optional[uuid.UUID] = None,
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
        self.performance_obligation_id = performance_obligation_id


class MockCustomerPayment:
    """Mock CustomerPayment model."""

    def __init__(
        self,
        payment_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        customer_id: uuid.UUID = None,
        payment_number: str = "RCP-0001",
        payment_date: date = None,
        payment_method: PaymentMethod = PaymentMethod.CHECK,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        amount: Decimal = Decimal("1000.00"),
        gross_amount: Optional[Decimal] = None,
        wht_amount: Decimal = Decimal("0"),
        wht_code_id: Optional[uuid.UUID] = None,
        wht_certificate_number: Optional[str] = None,
        functional_currency_amount: Decimal = Decimal("1000.00"),
        status: PaymentStatus = PaymentStatus.PENDING,
        bank_account_id: Optional[uuid.UUID] = None,
        reference: Optional[str] = None,
        created_by_user_id: Optional[uuid.UUID] = None,
        approved_by_user_id: Optional[uuid.UUID] = None,
        created_at: datetime = None,
        correlation_id: uuid.UUID = None,
    ):
        self.payment_id = payment_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.customer_id = customer_id or uuid.uuid4()
        self.payment_number = payment_number
        self.payment_date = payment_date or date.today()
        self.payment_method = payment_method
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.amount = amount
        self.gross_amount = gross_amount if gross_amount is not None else amount
        self.wht_amount = wht_amount
        self.wht_code_id = wht_code_id
        self.wht_certificate_number = wht_certificate_number
        self.functional_currency_amount = functional_currency_amount
        self.status = status
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.reference = reference
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.approved_by_user_id = approved_by_user_id
        self.created_at = created_at or datetime.now(timezone.utc)
        self.correlation_id = correlation_id or uuid.uuid4()
        self.allocations = []


class MockPaymentAllocation:
    """Mock PaymentAllocation model."""

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


class MockARAgingSnapshot:
    """Mock ARAgingSnapshot model."""

    def __init__(
        self,
        snapshot_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        snapshot_date: date = None,
        customer_id: Optional[uuid.UUID] = None,
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
        self.customer_id = customer_id
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
def mock_customer(organization_id) -> MockCustomer:
    """Create a mock customer."""
    return MockCustomer(organization_id=organization_id)


@pytest.fixture
def mock_invoice(organization_id, mock_customer) -> MockInvoice:
    """Create a mock invoice."""
    return MockInvoice(
        organization_id=organization_id,
        customer_id=mock_customer.customer_id,
    )


@pytest.fixture
def mock_customer_payment(organization_id, mock_customer) -> MockCustomerPayment:
    """Create a mock customer payment."""
    return MockCustomerPayment(
        organization_id=organization_id,
        customer_id=mock_customer.customer_id,
    )
