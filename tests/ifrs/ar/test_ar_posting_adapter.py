"""
Tests for ARPostingAdapter Service.

Tests cover:
- Invoice posting (standard, credit note, multi-currency)
- Payment posting
- Tax transaction creation
- Error handling and validation
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.models.finance.ar.invoice import InvoiceStatus, InvoiceType
from app.models.finance.ar.customer_payment import PaymentStatus
from app.services.finance.ar.ar_posting_adapter import ARPostingAdapter, ARPostingResult
from app.services.finance.ar.posting.helpers import create_tax_transactions


# ============ Mock Classes ============


class MockInvoice:
    """Mock Invoice for posting tests."""

    def __init__(
        self,
        invoice_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        customer_id: uuid.UUID = None,
        invoice_number: str = "INV-0001",
        invoice_type: InvoiceType = InvoiceType.STANDARD,
        invoice_date: date = None,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        exchange_rate_type_id: uuid.UUID = None,
        total_amount: Decimal = Decimal("1000.00"),
        functional_currency_amount: Decimal = Decimal("1000.00"),
        status: InvoiceStatus = InvoiceStatus.APPROVED,
        ar_control_account_id: uuid.UUID = None,
        correlation_id: uuid.UUID = None,
    ):
        self.invoice_id = invoice_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.customer_id = customer_id or uuid.uuid4()
        self.invoice_number = invoice_number
        self.invoice_type = invoice_type
        self.invoice_date = invoice_date or date.today()
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.exchange_rate_type_id = exchange_rate_type_id
        self.total_amount = total_amount
        self.functional_currency_amount = functional_currency_amount
        self.status = status
        self.ar_control_account_id = ar_control_account_id or uuid.uuid4()
        self.correlation_id = correlation_id or uuid.uuid4()


class MockInvoiceLine:
    """Mock InvoiceLine for posting tests."""

    # Sentinel to distinguish between None passed explicitly and default
    _DEFAULT = object()

    def __init__(
        self,
        line_id: uuid.UUID = None,
        invoice_id: uuid.UUID = None,
        line_number: int = 1,
        description: str = "Test service",
        line_amount: Decimal = Decimal("1000.00"),
        tax_amount: Decimal = Decimal("0"),
        tax_code_id: uuid.UUID = None,
        revenue_account_id: uuid.UUID = _DEFAULT,
        cost_center_id: uuid.UUID = None,
        project_id: uuid.UUID = None,
        segment_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.invoice_id = invoice_id or uuid.uuid4()
        self.line_number = line_number
        self.description = description
        self.line_amount = line_amount
        self.tax_amount = tax_amount
        self.tax_code_id = tax_code_id
        # Use default (generate UUID) only if not explicitly set
        if revenue_account_id is MockInvoiceLine._DEFAULT:
            self.revenue_account_id = uuid.uuid4()
        else:
            self.revenue_account_id = revenue_account_id
        self.cost_center_id = cost_center_id
        self.project_id = project_id
        self.segment_id = segment_id
        self.item_id = item_id


class MockCustomer:
    """Mock Customer for posting tests."""

    def __init__(
        self,
        customer_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        legal_name: str = "Test Customer Ltd",
        tax_identification_number: str = "TAX123456",
        ar_control_account_id: uuid.UUID = None,
        default_revenue_account_id: uuid.UUID = None,
    ):
        self.customer_id = customer_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.legal_name = legal_name
        self.tax_identification_number = tax_identification_number
        self.ar_control_account_id = ar_control_account_id or uuid.uuid4()
        self.default_revenue_account_id = default_revenue_account_id or uuid.uuid4()


class MockCustomerPayment:
    """Mock CustomerPayment for posting tests."""

    def __init__(
        self,
        payment_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        customer_id: uuid.UUID = None,
        payment_number: str = "PAY-0001",
        payment_date: date = None,
        reference: str = "REF001",
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        amount: Decimal = Decimal("1000.00"),
        status: PaymentStatus = PaymentStatus.APPROVED,
        bank_account_id: uuid.UUID = None,
        correlation_id: uuid.UUID = None,
    ):
        self.payment_id = payment_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.customer_id = customer_id or uuid.uuid4()
        self.payment_number = payment_number
        self.payment_date = payment_date or date.today()
        self.reference = reference
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.amount = amount
        self.status = status
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.correlation_id = correlation_id or uuid.uuid4()


class MockJournal:
    """Mock Journal entry."""

    def __init__(self, journal_entry_id: uuid.UUID = None):
        self.journal_entry_id = journal_entry_id or uuid.uuid4()


class MockPostingResult:
    """Mock LedgerPostingResult."""

    def __init__(
        self,
        success: bool = True,
        posting_batch_id: uuid.UUID = None,
        message: str = "Posted successfully",
    ):
        self.success = success
        self.posting_batch_id = posting_batch_id or uuid.uuid4()
        self.message = message


class MockFiscalPeriod:
    """Mock FiscalPeriod."""

    def __init__(
        self,
        fiscal_period_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        start_date: date = None,
        end_date: date = None,
    ):
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.start_date = start_date or date(2024, 1, 1)
        self.end_date = end_date or date(2024, 12, 31)


# ============ Fixtures ============


@pytest.fixture
def organization_id():
    return uuid.uuid4()


@pytest.fixture
def user_id():
    return uuid.uuid4()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    db.query = MagicMock(return_value=db)
    db.filter = MagicMock(return_value=db)
    db.order_by = MagicMock(return_value=db)
    db.all = MagicMock(return_value=[])
    db.first = MagicMock(return_value=None)
    return db


@pytest.fixture
def mock_invoice(organization_id):
    return MockInvoice(organization_id=organization_id)


@pytest.fixture
def mock_invoice_line(mock_invoice):
    return MockInvoiceLine(invoice_id=mock_invoice.invoice_id)


@pytest.fixture
def mock_customer(organization_id):
    return MockCustomer(organization_id=organization_id)


@pytest.fixture
def mock_payment(organization_id, mock_customer):
    return MockCustomerPayment(
        organization_id=organization_id,
        customer_id=mock_customer.customer_id,
    )


# ============ Post Invoice Tests ============


class TestPostInvoice:
    """Tests for ARPostingAdapter.post_invoice()."""

    def test_post_invoice_not_found(self, mock_db, organization_id, user_id):
        """Test posting when invoice not found."""
        mock_db.get.return_value = None

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=uuid.uuid4(),
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_invoice_wrong_organization(
        self, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test posting invoice from different organization."""
        mock_invoice.organization_id = uuid.uuid4()  # Different org
        mock_db.get.return_value = mock_invoice

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_invoice_not_approved(
        self, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test posting invoice that is not approved."""
        mock_invoice.status = InvoiceStatus.DRAFT
        mock_db.get.return_value = mock_invoice

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "APPROVED" in result.message

    def test_post_invoice_customer_not_found(
        self, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test posting when customer not found."""

        def get_side_effect(model, id):
            from app.models.finance.ar.invoice import Invoice

            if model == Invoice or str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            return None

        mock_db.get.side_effect = get_side_effect

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Customer not found" in result.message

    def test_post_invoice_no_lines(
        self, mock_db, organization_id, user_id, mock_invoice, mock_customer
    ):
        """Test posting invoice with no lines."""

        def get_side_effect(model, id):
            from app.models.finance.ar.invoice import Invoice
            from app.models.finance.ar.customer import Customer

            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_invoice.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
            []
        )

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "no lines" in result.message.lower()

    def test_post_invoice_no_revenue_account(
        self, mock_db, organization_id, user_id, mock_invoice, mock_customer
    ):
        """Test posting invoice line without revenue account."""
        line = MockInvoiceLine(
            invoice_id=mock_invoice.invoice_id, revenue_account_id=None
        )
        mock_customer.default_revenue_account_id = None

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_invoice.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            line
        ]

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "No revenue account" in result.message

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    @patch("app.services.finance.ar.posting.invoice.LedgerPostingService")
    def test_post_invoice_success(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_customer,
        mock_invoice_line,
    ):
        """Test successful invoice posting."""
        mock_invoice.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_invoice_line
        ]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal
        mock_journal_service.submit_journal.return_value = None
        mock_journal_service.approve_journal.return_value = None

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == journal.journal_entry_id
        assert "successfully" in result.message.lower()

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    @patch("app.services.finance.ar.posting.invoice.LedgerPostingService")
    def test_post_credit_note(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_customer,
    ):
        """Test posting a credit note (reverses debit/credit)."""
        credit_note = MockInvoice(
            organization_id=organization_id,
            customer_id=mock_customer.customer_id,
            invoice_type=InvoiceType.CREDIT_NOTE,
            total_amount=Decimal("-500.00"),
            functional_currency_amount=Decimal("-500.00"),
        )
        line = MockInvoiceLine(
            invoice_id=credit_note.invoice_id,
            line_amount=Decimal("-500.00"),
        )

        def get_side_effect(model, id):
            if str(id) == str(credit_note.invoice_id):
                return credit_note
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            line
        ]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=credit_note.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        # Verify journal was created with credit note logic
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]  # Third positional arg
        # First line should have credit (not debit) for credit note
        assert journal_input.lines[0].credit_amount > Decimal("0")

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    @patch("app.services.finance.ar.posting.invoice.LedgerPostingService")
    def test_post_multicurrency_invoice(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_customer,
    ):
        """Test posting invoice with foreign currency."""
        invoice = MockInvoice(
            organization_id=organization_id,
            customer_id=mock_customer.customer_id,
            currency_code="EUR",
            exchange_rate=Decimal("1.10"),  # 1 EUR = 1.10 USD
            total_amount=Decimal("1000.00"),  # EUR
            functional_currency_amount=Decimal("1100.00"),  # USD
        )
        line = MockInvoiceLine(
            invoice_id=invoice.invoice_id,
            line_amount=Decimal("1000.00"),
        )

        def get_side_effect(model, id):
            if str(id) == str(invoice.invoice_id):
                return invoice
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            line
        ]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        # Verify functional amounts were calculated
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        assert journal_input.currency_code == "EUR"
        assert journal_input.exchange_rate == Decimal("1.10")

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    def test_post_invoice_journal_creation_failure(
        self,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_customer,
        mock_invoice_line,
    ):
        """Test handling of journal creation failure."""
        from fastapi import HTTPException

        mock_invoice.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_invoice_line
        ]

        mock_journal_service.create_journal.side_effect = HTTPException(
            status_code=400, detail="Period closed"
        )

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Journal creation failed" in result.message

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    @patch("app.services.finance.ar.posting.invoice.LedgerPostingService")
    def test_post_invoice_ledger_posting_failure(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_customer,
        mock_invoice_line,
    ):
        """Test handling of ledger posting failure."""
        mock_invoice.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_invoice_line
        ]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(
            success=False, message="Insufficient balance"
        )
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Ledger posting failed" in result.message
        assert result.journal_entry_id == journal.journal_entry_id

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    @patch("app.services.finance.ar.posting.invoice.LedgerPostingService")
    def test_post_invoice_with_idempotency_key(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_customer,
        mock_invoice_line,
    ):
        """Test posting with custom idempotency key."""
        mock_invoice.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_invoice_line
        ]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        custom_key = "my-custom-key-123"
        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
            idempotency_key=custom_key,
        )

        assert result.success is True
        # Verify the idempotency key was passed
        call_args = mock_ledger_service.post_journal_entry.call_args
        posting_request = call_args[0][1]
        assert posting_request.idempotency_key == custom_key

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    @patch("app.services.finance.ar.posting.invoice.LedgerPostingService")
    def test_post_invoice_with_cost_centers(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_customer,
    ):
        """Test posting invoice with cost center, project, and segment."""
        mock_invoice.customer_id = mock_customer.customer_id
        cost_center_id = uuid.uuid4()
        project_id = uuid.uuid4()
        segment_id = uuid.uuid4()

        line = MockInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            cost_center_id=cost_center_id,
            project_id=project_id,
            segment_id=segment_id,
        )

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            line
        ]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        # Verify dimensions were passed to journal lines
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        revenue_line = journal_input.lines[1]  # Second line is revenue
        assert revenue_line.cost_center_id == cost_center_id
        assert revenue_line.project_id == project_id
        assert revenue_line.segment_id == segment_id

    @patch("app.services.finance.ar.posting.invoice.JournalService")
    @patch("app.services.finance.ar.posting.invoice.LedgerPostingService")
    def test_post_invoice_exception_handling(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_customer,
        mock_invoice_line,
    ):
        """Test exception handling during posting."""
        mock_invoice.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            mock_invoice_line
        ]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        mock_ledger_service.post_journal_entry.side_effect = Exception(
            "Database connection lost"
        )

        result = ARPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Posting error" in result.message
        assert result.journal_entry_id == journal.journal_entry_id


# ============ Post Payment Tests ============


class TestPostPayment:
    """Tests for ARPostingAdapter.post_payment()."""

    def test_post_payment_not_found(self, mock_db, organization_id, user_id):
        """Test posting when payment not found."""
        mock_db.get.return_value = None

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=uuid.uuid4(),
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_payment_wrong_organization(
        self, mock_db, organization_id, user_id, mock_payment
    ):
        """Test posting payment from different organization."""
        mock_payment.organization_id = uuid.uuid4()  # Different org
        mock_db.get.return_value = mock_payment

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_payment_not_approved(
        self, mock_db, organization_id, user_id, mock_payment
    ):
        """Test posting payment that is not approved."""
        mock_payment.status = PaymentStatus.PENDING
        mock_db.get.return_value = mock_payment

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "APPROVED" in result.message

    def test_post_payment_customer_not_found(
        self, mock_db, organization_id, user_id, mock_payment
    ):
        """Test posting when customer not found."""

        def get_side_effect(model, id):
            from app.models.finance.ar.customer_payment import CustomerPayment

            if model == CustomerPayment or str(id) == str(mock_payment.payment_id):
                return mock_payment
            return None

        mock_db.get.side_effect = get_side_effect

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Customer not found" in result.message

    @patch("app.services.finance.ar.posting.payment.JournalService")
    @patch("app.services.finance.ar.posting.payment.LedgerPostingService")
    def test_post_payment_success(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_customer,
    ):
        """Test successful payment posting."""
        mock_payment.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == journal.journal_entry_id
        assert "successfully" in result.message.lower()

        # Verify journal lines (Debit Bank, Credit AR)
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        assert len(journal_input.lines) == 2
        assert journal_input.lines[0].debit_amount == mock_payment.amount
        assert journal_input.lines[1].credit_amount == mock_payment.amount

    @patch("app.services.finance.ar.posting.payment.JournalService")
    @patch("app.services.finance.ar.posting.payment.LedgerPostingService")
    def test_post_multicurrency_payment(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_customer,
    ):
        """Test posting payment in foreign currency."""
        payment = MockCustomerPayment(
            organization_id=organization_id,
            customer_id=mock_customer.customer_id,
            currency_code="GBP",
            exchange_rate=Decimal("1.25"),
            amount=Decimal("800.00"),  # GBP
        )

        def get_side_effect(model, id):
            if str(id) == str(payment.payment_id):
                return payment
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        # Functional amount = 800 * 1.25 = 1000
        assert journal_input.lines[0].debit_amount_functional == Decimal("1000.00")

    @patch("app.services.finance.ar.posting.payment.JournalService")
    def test_post_payment_journal_failure(
        self,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_customer,
    ):
        """Test handling of journal creation failure for payment."""
        from fastapi import HTTPException

        mock_payment.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect

        mock_journal_service.create_journal.side_effect = HTTPException(
            status_code=400, detail="Invalid account"
        )

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Journal creation failed" in result.message

    @patch("app.services.finance.ar.posting.payment.JournalService")
    @patch("app.services.finance.ar.posting.payment.LedgerPostingService")
    def test_post_payment_ledger_failure(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_customer,
    ):
        """Test handling of ledger posting failure for payment."""
        mock_payment.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=False, message="Period locked")
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Ledger posting failed" in result.message

    @patch("app.services.finance.ar.posting.payment.JournalService")
    @patch("app.services.finance.ar.posting.payment.LedgerPostingService")
    def test_post_payment_exception_handling(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_customer,
    ):
        """Test exception handling during payment posting."""
        mock_payment.customer_id = mock_customer.customer_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_customer.customer_id):
                return mock_customer
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        mock_ledger_service.post_journal_entry.side_effect = Exception(
            "Network timeout"
        )

        result = ARPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Posting error" in result.message


# ============ Tax Transaction Creation Tests ============


class TestCreateTaxTransactions:
    """Tests for create_tax_transactions()."""

    @patch("app.services.finance.ar.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_no_fiscal_period(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_customer
    ):
        """Test when no fiscal period exists for invoice date."""
        mock_invoice_line = MockInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=uuid.uuid4(),
            tax_amount=Decimal("100.00"),
        )

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[mock_invoice_line],
            customer=mock_customer,
            exchange_rate=Decimal("1.0"),
        )

        assert result == []
        mock_tax_service.create_from_invoice_line.assert_not_called()

    @patch("app.services.finance.ar.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_no_tax_code(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_customer
    ):
        """Test lines without tax codes are skipped."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_no_tax = MockInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=None,  # No tax
            tax_amount=Decimal("0"),
        )

        mock_db.query.return_value.filter.return_value.first.return_value = fiscal_period

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_no_tax],
            customer=mock_customer,
            exchange_rate=Decimal("1.0"),
        )

        assert result == []
        mock_tax_service.create_from_invoice_line.assert_not_called()

    @patch("app.services.finance.ar.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_zero_tax(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_customer
    ):
        """Test lines with zero tax amount are skipped."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_zero_tax = MockInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=uuid.uuid4(),  # Has tax code
            tax_amount=Decimal("0"),  # But zero amount
        )

        mock_db.query.return_value.filter.return_value.first.return_value = fiscal_period

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_zero_tax],
            customer=mock_customer,
            exchange_rate=Decimal("1.0"),
        )

        assert result == []
        mock_tax_service.create_from_invoice_line.assert_not_called()

    @patch("app.services.finance.ar.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_success(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_customer
    ):
        """Test successful tax transaction creation."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        tax_code_id = uuid.uuid4()
        line_with_tax = MockInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=tax_code_id,
            line_amount=Decimal("1000.00"),
            tax_amount=Decimal("100.00"),
        )

        mock_db.query.return_value.filter.return_value.first.return_value = fiscal_period

        tax_txn_id = uuid.uuid4()
        mock_tax_txn = MagicMock()
        mock_tax_txn.transaction_id = tax_txn_id
        mock_tax_service.create_from_invoice_line.return_value = mock_tax_txn

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_with_tax],
            customer=mock_customer,
            exchange_rate=Decimal("1.0"),
        )

        assert len(result) == 1
        assert result[0] == tax_txn_id
        mock_tax_service.create_from_invoice_line.assert_called_once()
        call_kwargs = mock_tax_service.create_from_invoice_line.call_args[1]
        assert call_kwargs["is_purchase"] is False  # AR = OUTPUT tax
        assert call_kwargs["base_amount"] == Decimal("1000.00")

    @patch("app.services.finance.ar.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_credit_note(
        self, mock_tax_service, mock_db, organization_id, mock_customer
    ):
        """Test tax transaction for credit note (negative amounts)."""
        credit_note = MockInvoice(
            organization_id=organization_id,
            invoice_type=InvoiceType.CREDIT_NOTE,
        )
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_with_tax = MockInvoiceLine(
            invoice_id=credit_note.invoice_id,
            tax_code_id=uuid.uuid4(),
            line_amount=Decimal("500.00"),
            tax_amount=Decimal("50.00"),
        )

        mock_db.query.return_value.filter.return_value.first.return_value = fiscal_period

        mock_tax_txn = MagicMock()
        mock_tax_txn.transaction_id = uuid.uuid4()
        mock_tax_service.create_from_invoice_line.return_value = mock_tax_txn

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=credit_note,
            lines=[line_with_tax],
            customer=mock_customer,
            exchange_rate=Decimal("1.0"),
            is_credit_note=True,
        )

        assert len(result) == 1
        call_kwargs = mock_tax_service.create_from_invoice_line.call_args[1]
        # Credit note should have negative base amount
        assert call_kwargs["base_amount"] == Decimal("-500.00")

    @patch("app.services.finance.ar.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_exception_handling(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_customer
    ):
        """Test that exceptions in tax creation don't fail posting."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_with_tax = MockInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=uuid.uuid4(),
            tax_amount=Decimal("100.00"),
        )

        mock_db.query.return_value.filter.return_value.first.return_value = fiscal_period

        mock_tax_service.create_from_invoice_line.side_effect = Exception(
            "Tax service error"
        )

        # Should not raise, just return empty list
        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_with_tax],
            customer=mock_customer,
            exchange_rate=Decimal("1.0"),
        )

        assert result == []

    @patch("app.services.finance.ar.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_multiple_lines(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_customer
    ):
        """Test creating tax transactions for multiple lines."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)

        lines = [
            MockInvoiceLine(
                invoice_id=mock_invoice.invoice_id,
                line_number=1,
                tax_code_id=uuid.uuid4(),
                line_amount=Decimal("1000.00"),
                tax_amount=Decimal("100.00"),
            ),
            MockInvoiceLine(
                invoice_id=mock_invoice.invoice_id,
                line_number=2,
                tax_code_id=None,  # No tax - should be skipped
                tax_amount=Decimal("0"),
            ),
            MockInvoiceLine(
                invoice_id=mock_invoice.invoice_id,
                line_number=3,
                tax_code_id=uuid.uuid4(),
                line_amount=Decimal("500.00"),
                tax_amount=Decimal("50.00"),
            ),
        ]

        mock_db.query.return_value.filter.return_value.first.return_value = fiscal_period

        # Return different IDs for each call
        mock_tax_service.create_from_invoice_line.side_effect = [
            MagicMock(transaction_id=uuid.uuid4()),
            MagicMock(transaction_id=uuid.uuid4()),
        ]

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=lines,
            customer=mock_customer,
            exchange_rate=Decimal("1.0"),
        )

        # Should have 2 transactions (line 2 skipped)
        assert len(result) == 2
        assert mock_tax_service.create_from_invoice_line.call_count == 2


# ============ ARPostingResult Tests ============


class TestARPostingResult:
    """Tests for ARPostingResult dataclass."""

    def test_result_success(self):
        """Test successful result creation."""
        journal_id = uuid.uuid4()
        batch_id = uuid.uuid4()

        result = ARPostingResult(
            success=True,
            journal_entry_id=journal_id,
            posting_batch_id=batch_id,
            message="Posted successfully",
        )

        assert result.success is True
        assert result.journal_entry_id == journal_id
        assert result.posting_batch_id == batch_id
        assert result.message == "Posted successfully"

    def test_result_failure(self):
        """Test failure result creation."""
        result = ARPostingResult(success=False, message="Validation failed")

        assert result.success is False
        assert result.journal_entry_id is None
        assert result.posting_batch_id is None
        assert result.message == "Validation failed"

    def test_result_defaults(self):
        """Test default values."""
        result = ARPostingResult(success=True)

        assert result.journal_entry_id is None
        assert result.posting_batch_id is None
        assert result.message == ""
