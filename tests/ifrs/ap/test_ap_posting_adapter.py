"""
Tests for APPostingAdapter Service.

Tests cover:
- Supplier invoice posting (standard, credit note, multi-currency)
- Supplier payment posting
- Invoice reversal
- Tax transaction creation
- Error handling and validation
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_payment import APPaymentStatus
from app.services.finance.ap.ap_posting_adapter import APPostingAdapter, APPostingResult
from app.services.finance.ap.posting.helpers import create_tax_transactions

# ============ Mock Classes ============


class MockSupplierInvoice:
    """Mock SupplierInvoice for posting tests."""

    def __init__(
        self,
        invoice_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        supplier_id: uuid.UUID = None,
        invoice_number: str = "AP-INV-0001",
        supplier_invoice_number: str = "SUPP-001",
        invoice_type: SupplierInvoiceType = SupplierInvoiceType.STANDARD,
        invoice_date: date = None,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        exchange_rate_type_id: uuid.UUID = None,
        total_amount: Decimal = Decimal("1000.00"),
        functional_currency_amount: Decimal = Decimal("1000.00"),
        status: SupplierInvoiceStatus = SupplierInvoiceStatus.APPROVED,
        ap_control_account_id: uuid.UUID = None,
        journal_entry_id: uuid.UUID = None,
        correlation_id: uuid.UUID = None,
        stamp_duty_amount: Decimal = Decimal("0"),
        stamp_duty_code_id: uuid.UUID = None,
    ):
        self.invoice_id = invoice_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.supplier_id = supplier_id or uuid.uuid4()
        self.invoice_number = invoice_number
        self.supplier_invoice_number = supplier_invoice_number
        self.invoice_type = invoice_type
        self.invoice_date = invoice_date or date.today()
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.exchange_rate_type_id = exchange_rate_type_id
        self.total_amount = total_amount
        self.functional_currency_amount = functional_currency_amount
        self.status = status
        self.ap_control_account_id = ap_control_account_id or uuid.uuid4()
        self.journal_entry_id = journal_entry_id
        self.correlation_id = correlation_id or uuid.uuid4()
        self.stamp_duty_amount = stamp_duty_amount
        self.stamp_duty_code_id = stamp_duty_code_id


class MockSupplierInvoiceLine:
    """Mock SupplierInvoiceLine for posting tests."""

    # Sentinel to distinguish between None passed explicitly and default
    _DEFAULT = object()

    def __init__(
        self,
        line_id: uuid.UUID = None,
        invoice_id: uuid.UUID = None,
        line_number: int = 1,
        description: str = "Office supplies",
        line_amount: Decimal = Decimal("1000.00"),
        tax_amount: Decimal = Decimal("0"),
        tax_code_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        goods_receipt_line_id: uuid.UUID = None,
        expense_account_id: uuid.UUID = _DEFAULT,
        capitalize_flag: bool = False,
        asset_account_id: uuid.UUID = None,
        asset_category_id: uuid.UUID = None,
        cost_center_id: uuid.UUID = None,
        project_id: uuid.UUID = None,
        segment_id: uuid.UUID = None,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.invoice_id = invoice_id or uuid.uuid4()
        self.line_number = line_number
        self.description = description
        self.line_amount = line_amount
        self.tax_amount = tax_amount
        self.tax_code_id = tax_code_id
        self.item_id = item_id
        self.goods_receipt_line_id = goods_receipt_line_id
        self.capitalize_flag = capitalize_flag
        if expense_account_id is MockSupplierInvoiceLine._DEFAULT:
            self.expense_account_id = uuid.uuid4()
        else:
            self.expense_account_id = expense_account_id
        self.asset_account_id = asset_account_id
        self.asset_category_id = asset_category_id
        self.cost_center_id = cost_center_id
        self.project_id = project_id
        self.segment_id = segment_id


class MockSupplier:
    """Mock Supplier for posting tests."""

    def __init__(
        self,
        supplier_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        legal_name: str = "Test Supplier Inc",
        tax_id: str = "TAX789012",
        tax_identification_number: str = None,
        ap_control_account_id: uuid.UUID = None,
        default_expense_account_id: uuid.UUID = None,
    ):
        self.supplier_id = supplier_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.legal_name = legal_name
        self.tax_id = tax_id
        self.tax_identification_number = tax_identification_number or tax_id
        self.ap_control_account_id = ap_control_account_id or uuid.uuid4()
        self.default_expense_account_id = default_expense_account_id or uuid.uuid4()


class MockSupplierPayment:
    """Mock SupplierPayment for posting tests."""

    def __init__(
        self,
        payment_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        supplier_id: uuid.UUID = None,
        payment_number: str = "AP-PAY-0001",
        payment_date: date = None,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        payment_amount: Decimal = Decimal("1000.00"),
        amount: Decimal = None,
        gross_amount: Decimal = None,
        withholding_tax_amount: Decimal = None,
        withholding_tax_code_id: uuid.UUID = None,
        status: APPaymentStatus = APPaymentStatus.APPROVED,
        bank_account_id: uuid.UUID = None,
        correlation_id: uuid.UUID = None,
    ):
        self.payment_id = payment_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.supplier_id = supplier_id or uuid.uuid4()
        self.payment_number = payment_number
        self.payment_date = payment_date or date.today()
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.payment_amount = payment_amount
        self.amount = amount if amount is not None else payment_amount
        self.gross_amount = gross_amount
        self.withholding_tax_amount = withholding_tax_amount
        self.withholding_tax_code_id = withholding_tax_code_id
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


class MockReversalResult:
    """Mock ReversalService result."""

    def __init__(
        self,
        success: bool = True,
        reversal_journal_id: uuid.UUID = None,
        message: str = "Reversed successfully",
    ):
        self.success = success
        self.reversal_journal_id = reversal_journal_id or uuid.uuid4()
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
    # db.query removed - using select() now
    db.filter = MagicMock(return_value=db)
    db.order_by = MagicMock(return_value=db)
    db.all = MagicMock(return_value=[])
    db.first = MagicMock(return_value=None)
    # Secondary idempotency guard uses db.scalar() — must return None by default
    # so tests proceed past the "already posted" check
    db.scalar = MagicMock(return_value=None)
    return db


@pytest.fixture
def mock_invoice(organization_id):
    return MockSupplierInvoice(organization_id=organization_id)


@pytest.fixture
def mock_invoice_line(mock_invoice):
    return MockSupplierInvoiceLine(invoice_id=mock_invoice.invoice_id)


@pytest.fixture
def mock_supplier(organization_id):
    return MockSupplier(organization_id=organization_id)


@pytest.fixture
def mock_payment(organization_id, mock_supplier):
    return MockSupplierPayment(
        organization_id=organization_id,
        supplier_id=mock_supplier.supplier_id,
    )


# ============ Post Invoice Tests ============


class TestPostInvoice:
    """Tests for APPostingAdapter.post_invoice()."""

    def test_post_invoice_not_found(self, mock_db, organization_id, user_id):
        """Test posting when invoice not found."""
        mock_db.get.return_value = None

        result = APPostingAdapter.post_invoice(
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

        result = APPostingAdapter.post_invoice(
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
        mock_invoice.status = SupplierInvoiceStatus.DRAFT
        mock_db.get.return_value = mock_invoice

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "APPROVED" in result.message

    def test_post_invoice_supplier_not_found(
        self, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test posting when supplier not found."""

        def get_side_effect(model, id):
            from app.models.finance.ap.supplier_invoice import SupplierInvoice

            if model == SupplierInvoice or str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            return None

        mock_db.get.side_effect = get_side_effect

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Supplier not found" in result.message

    def test_post_invoice_no_lines(
        self, mock_db, organization_id, user_id, mock_invoice, mock_supplier
    ):
        """Test posting invoice with no lines."""

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_invoice.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = []

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "no lines" in result.message.lower()

    def test_post_invoice_no_expense_account(
        self, mock_db, organization_id, user_id, mock_invoice, mock_supplier
    ):
        """Test posting invoice line without expense account."""
        line = MockSupplierInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            expense_account_id=None,
            asset_account_id=None,
        )
        mock_supplier.default_expense_account_id = None

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_invoice.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [line]

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "No expense account" in result.message

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_invoice_success(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_supplier,
        mock_invoice_line,
    ):
        """Test successful invoice posting."""
        mock_invoice.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [mock_invoice_line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal
        mock_journal_service.submit_journal.return_value = None
        mock_journal_service.approve_journal.return_value = None

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == journal.journal_entry_id
        assert "successfully" in result.message.lower()

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_credit_note(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_supplier,
    ):
        """Test posting a credit note (reverses debit/credit)."""
        credit_note = MockSupplierInvoice(
            organization_id=organization_id,
            supplier_id=mock_supplier.supplier_id,
            invoice_type=SupplierInvoiceType.CREDIT_NOTE,
            total_amount=Decimal("-500.00"),
            functional_currency_amount=Decimal("-500.00"),
        )
        line = MockSupplierInvoiceLine(
            invoice_id=credit_note.invoice_id,
            line_amount=Decimal("-500.00"),
        )

        def get_side_effect(model, id):
            if str(id) == str(credit_note.invoice_id):
                return credit_note
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_invoice(
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
        # First line should have credit (not debit) for credit note expense
        assert journal_input.lines[0].credit_amount > Decimal("0")

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_multicurrency_invoice(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_supplier,
    ):
        """Test posting invoice with foreign currency."""
        invoice = MockSupplierInvoice(
            organization_id=organization_id,
            supplier_id=mock_supplier.supplier_id,
            currency_code="EUR",
            exchange_rate=Decimal("1.10"),
            total_amount=Decimal("1000.00"),
            functional_currency_amount=Decimal("1100.00"),
        )
        line = MockSupplierInvoiceLine(
            invoice_id=invoice.invoice_id,
            line_amount=Decimal("1000.00"),
        )

        def get_side_effect(model, id):
            if str(id) == str(invoice.invoice_id):
                return invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        assert journal_input.currency_code == "EUR"
        assert journal_input.exchange_rate == Decimal("1.10")

    @patch("app.services.finance.posting.base.JournalService")
    def test_post_invoice_journal_creation_failure(
        self,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_supplier,
        mock_invoice_line,
    ):
        """Test handling of journal creation failure."""
        from fastapi import HTTPException

        mock_invoice.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [mock_invoice_line]

        mock_journal_service.create_journal.side_effect = HTTPException(
            status_code=400, detail="Period closed"
        )

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Journal creation failed" in result.message

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_invoice_ledger_posting_failure(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_supplier,
        mock_invoice_line,
    ):
        """Test handling of ledger posting failure."""
        mock_invoice.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [mock_invoice_line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(
            success=False, message="Insufficient balance"
        )
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Ledger posting failed" in result.message
        assert result.journal_entry_id == journal.journal_entry_id

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_invoice_with_idempotency_key(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_supplier,
        mock_invoice_line,
    ):
        """Test posting with custom idempotency key."""
        mock_invoice.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [mock_invoice_line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        custom_key = "my-custom-key-456"
        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
            idempotency_key=custom_key,
        )

        assert result.success is True
        call_args = mock_ledger_service.post_journal_entry.call_args
        posting_request = call_args[0][1]
        assert posting_request.idempotency_key == custom_key

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_invoice_with_cost_centers(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_supplier,
    ):
        """Test posting invoice with cost center, project, and segment."""
        mock_invoice.supplier_id = mock_supplier.supplier_id
        cost_center_id = uuid.uuid4()
        project_id = uuid.uuid4()
        segment_id = uuid.uuid4()

        line = MockSupplierInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            cost_center_id=cost_center_id,
            project_id=project_id,
            segment_id=segment_id,
        )

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        expense_line = journal_input.lines[0]  # First line is expense
        assert expense_line.cost_center_id == cost_center_id
        assert expense_line.project_id == project_id
        assert expense_line.segment_id == segment_id

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_invoice_exception_handling(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_supplier,
        mock_invoice_line,
    ):
        """Test exception handling during posting."""
        mock_invoice.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [mock_invoice_line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        mock_ledger_service.post_journal_entry.side_effect = Exception(
            "Database connection lost"
        )

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Ledger posting failed" in result.message
        assert result.journal_entry_id == journal.journal_entry_id

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_invoice_uses_asset_account(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_invoice,
        mock_supplier,
    ):
        """Test posting with asset account instead of expense account."""
        mock_invoice.supplier_id = mock_supplier.supplier_id
        asset_account_id = uuid.uuid4()

        line = MockSupplierInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            expense_account_id=None,
            asset_account_id=asset_account_id,
        )

        def get_side_effect(model, id):
            if str(id) == str(mock_invoice.invoice_id):
                return mock_invoice
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            return None

        mock_db.get.side_effect = get_side_effect
        mock_db.scalars.return_value.all.return_value = [line]

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_invoice(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        assert journal_input.lines[0].account_id == asset_account_id


# ============ Post Payment Tests ============


class TestPostPayment:
    """Tests for APPostingAdapter.post_payment()."""

    def test_post_payment_not_found(self, mock_db, organization_id, user_id):
        """Test posting when payment not found."""
        mock_db.get.return_value = None

        result = APPostingAdapter.post_payment(
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
        mock_payment.organization_id = uuid.uuid4()
        mock_db.get.return_value = mock_payment

        result = APPostingAdapter.post_payment(
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
        mock_payment.status = APPaymentStatus.DRAFT
        mock_db.get.return_value = mock_payment

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "APPROVED" in result.message

    def test_post_payment_supplier_not_found(
        self, mock_db, organization_id, user_id, mock_payment
    ):
        """Test posting when supplier not found."""

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            return None

        mock_db.get.side_effect = get_side_effect

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Supplier not found" in result.message

    def test_post_payment_rejects_unmapped_bank_account(
        self, mock_db, organization_id, user_id, mock_payment, mock_supplier
    ):
        """Test posting fails when bank account is not mapped to GL."""
        mock_payment.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            if model.__name__ in {"Account", "BankAccount"}:
                return None
            return None

        mock_db.get.side_effect = get_side_effect

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not mapped to a valid GL account" in result.message

    def test_post_payment_rejects_non_withholding_tax_code(
        self, mock_db, organization_id, user_id, mock_payment, mock_supplier
    ):
        """Test posting fails when WHT tax code is not WITHHOLDING."""
        mock_payment.supplier_id = mock_supplier.supplier_id
        mock_payment.gross_amount = Decimal("100.00")
        mock_payment.amount = Decimal("90.00")
        mock_payment.withholding_tax_amount = Decimal("10.00")
        mock_payment.withholding_tax_code_id = uuid.uuid4()

        invalid_tax_code = MagicMock(
            organization_id=organization_id,
            tax_type="VAT",
            tax_collected_account_id=uuid.uuid4(),
        )

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            if model.__name__ == "Account" and str(id) == str(
                mock_payment.bank_account_id
            ):
                return MagicMock(organization_id=organization_id)
            if str(id) == str(mock_payment.withholding_tax_code_id):
                return invalid_tax_code
            return None

        mock_db.get.side_effect = get_side_effect

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not a WITHHOLDING tax code" in result.message

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_payment_success(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_supplier,
    ):
        """Test successful payment posting."""
        mock_payment.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            if model.__name__ == "Account" and str(id) == str(
                mock_payment.bank_account_id
            ):
                return MagicMock(organization_id=organization_id)
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == journal.journal_entry_id
        assert "successfully" in result.message.lower()

        # Verify journal lines (Debit AP, Credit Bank)
        call_args = mock_journal_service.create_journal.call_args
        journal_input = call_args[0][2]
        assert len(journal_input.lines) == 2
        assert journal_input.lines[0].debit_amount == mock_payment.payment_amount
        assert journal_input.lines[1].credit_amount == mock_payment.payment_amount

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_multicurrency_payment(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_supplier,
    ):
        """Test posting payment in foreign currency."""
        payment = MockSupplierPayment(
            organization_id=organization_id,
            supplier_id=mock_supplier.supplier_id,
            currency_code="GBP",
            exchange_rate=Decimal("1.25"),
            payment_amount=Decimal("800.00"),
        )

        def get_side_effect(model, id):
            if str(id) == str(payment.payment_id):
                return payment
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            if model.__name__ == "Account" and str(id) == str(payment.bank_account_id):
                return MagicMock(organization_id=organization_id)
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=True)
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_payment(
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

    @patch("app.services.finance.posting.base.JournalService")
    def test_post_payment_journal_failure(
        self,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_supplier,
    ):
        """Test handling of journal creation failure for payment."""
        from fastapi import HTTPException

        mock_payment.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            if model.__name__ == "Account" and str(id) == str(
                mock_payment.bank_account_id
            ):
                return MagicMock(organization_id=organization_id)
            return None

        mock_db.get.side_effect = get_side_effect

        mock_journal_service.create_journal.side_effect = HTTPException(
            status_code=400, detail="Invalid account"
        )

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Journal creation failed" in result.message

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_payment_ledger_failure(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_supplier,
    ):
        """Test handling of ledger posting failure for payment."""
        mock_payment.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            if model.__name__ == "Account" and str(id) == str(
                mock_payment.bank_account_id
            ):
                return MagicMock(organization_id=organization_id)
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        posting_result = MockPostingResult(success=False, message="Period locked")
        mock_ledger_service.post_journal_entry.return_value = posting_result

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Ledger posting failed" in result.message

    @patch("app.services.finance.posting.base.JournalService")
    @patch("app.services.finance.posting.base.LedgerPostingService")
    def test_post_payment_exception_handling(
        self,
        mock_ledger_service,
        mock_journal_service,
        mock_db,
        organization_id,
        user_id,
        mock_payment,
        mock_supplier,
    ):
        """Test exception handling during payment posting."""
        mock_payment.supplier_id = mock_supplier.supplier_id

        def get_side_effect(model, id):
            if str(id) == str(mock_payment.payment_id):
                return mock_payment
            if str(id) == str(mock_supplier.supplier_id):
                return mock_supplier
            if model.__name__ == "Account" and str(id) == str(
                mock_payment.bank_account_id
            ):
                return MagicMock(organization_id=organization_id)
            return None

        mock_db.get.side_effect = get_side_effect

        journal = MockJournal()
        mock_journal_service.create_journal.return_value = journal

        mock_ledger_service.post_journal_entry.side_effect = Exception(
            "Network timeout"
        )

        result = APPostingAdapter.post_payment(
            db=mock_db,
            organization_id=organization_id,
            payment_id=mock_payment.payment_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Ledger posting failed" in result.message


# ============ Reverse Invoice Posting Tests ============


class TestReverseInvoicePosting:
    """Tests for APPostingAdapter.reverse_invoice_posting()."""

    def test_reverse_invoice_not_found(self, mock_db, organization_id, user_id):
        """Test reversal when invoice not found."""
        mock_db.get.return_value = None

        result = APPostingAdapter.reverse_invoice_posting(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=uuid.uuid4(),
            reversal_date=date.today(),
            reversed_by_user_id=user_id,
            reason="Test reversal",
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_reverse_invoice_not_posted(
        self, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test reversal when invoice has not been posted."""
        mock_invoice.journal_entry_id = None
        mock_db.get.return_value = mock_invoice

        result = APPostingAdapter.reverse_invoice_posting(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            reversal_date=date.today(),
            reversed_by_user_id=user_id,
            reason="Test reversal",
        )

        assert result.success is False
        assert "not been posted" in result.message.lower()

    @patch("app.services.finance.gl.reversal.ReversalService")
    def test_reverse_invoice_success(
        self, mock_reversal_service, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test successful invoice reversal."""
        mock_invoice.journal_entry_id = uuid.uuid4()
        mock_db.get.return_value = mock_invoice

        reversal_result = MockReversalResult(success=True)
        mock_reversal_service.create_reversal.return_value = reversal_result

        result = APPostingAdapter.reverse_invoice_posting(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            reversal_date=date.today(),
            reversed_by_user_id=user_id,
            reason="Invoice entered in error",
        )

        assert result.success is True
        assert "reversed successfully" in result.message.lower()
        assert result.journal_entry_id == reversal_result.reversal_journal_id

    @patch("app.services.finance.gl.reversal.ReversalService")
    def test_reverse_invoice_failure(
        self, mock_reversal_service, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test reversal failure from ReversalService."""
        mock_invoice.journal_entry_id = uuid.uuid4()
        mock_db.get.return_value = mock_invoice

        reversal_result = MockReversalResult(
            success=False, message="Cannot reverse posted journal"
        )
        mock_reversal_service.create_reversal.return_value = reversal_result

        result = APPostingAdapter.reverse_invoice_posting(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            reversal_date=date.today(),
            reversed_by_user_id=user_id,
            reason="Test",
        )

        assert result.success is False
        assert reversal_result.message in result.message

    @patch("app.services.finance.gl.reversal.ReversalService")
    def test_reverse_invoice_http_exception(
        self, mock_reversal_service, mock_db, organization_id, user_id, mock_invoice
    ):
        """Test reversal with HTTP exception."""
        from fastapi import HTTPException

        mock_invoice.journal_entry_id = uuid.uuid4()
        mock_db.get.return_value = mock_invoice

        mock_reversal_service.create_reversal.side_effect = HTTPException(
            status_code=400, detail="Period closed for reversal"
        )

        result = APPostingAdapter.reverse_invoice_posting(
            db=mock_db,
            organization_id=organization_id,
            invoice_id=mock_invoice.invoice_id,
            reversal_date=date.today(),
            reversed_by_user_id=user_id,
            reason="Test",
        )

        assert result.success is False
        assert "Reversal failed" in result.message


# ============ Tax Transaction Creation Tests ============


class TestCreateTaxTransactions:
    """Tests for create_tax_transactions()."""

    @patch("app.services.finance.ap.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_no_fiscal_period(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_supplier
    ):
        """Test when no fiscal period exists for invoice date."""
        line_with_tax = MockSupplierInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=uuid.uuid4(),
            tax_amount=Decimal("100.00"),
        )

        mock_db.scalar.return_value = None

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_with_tax],
            supplier=mock_supplier,
            exchange_rate=Decimal("1.0"),
        )

        assert result == []
        mock_tax_service.create_from_invoice_line.assert_not_called()

    @patch("app.services.finance.ap.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_no_tax_code(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_supplier
    ):
        """Test lines without tax codes are skipped."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_no_tax = MockSupplierInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=None,
            tax_amount=Decimal("0"),
        )

        mock_db.scalar.return_value = fiscal_period

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_no_tax],
            supplier=mock_supplier,
            exchange_rate=Decimal("1.0"),
        )

        assert result == []
        mock_tax_service.create_from_invoice_line.assert_not_called()

    @patch("app.services.finance.ap.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_success(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_supplier
    ):
        """Test successful tax transaction creation for AP (INPUT tax)."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_with_tax = MockSupplierInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=uuid.uuid4(),
            line_amount=Decimal("1000.00"),
            tax_amount=Decimal("100.00"),
        )

        mock_db.scalar.return_value = fiscal_period

        tax_txn_id = uuid.uuid4()
        mock_tax_txn = MagicMock()
        mock_tax_txn.transaction_id = tax_txn_id
        mock_tax_service.create_from_invoice_line.return_value = mock_tax_txn

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_with_tax],
            supplier=mock_supplier,
            exchange_rate=Decimal("1.0"),
        )

        assert len(result) == 1
        assert result[0] == tax_txn_id
        mock_tax_service.create_from_invoice_line.assert_called_once()
        call_kwargs = mock_tax_service.create_from_invoice_line.call_args[1]
        # AP invoices create INPUT tax (purchases)
        assert call_kwargs["is_purchase"] is True
        assert call_kwargs["base_amount"] == Decimal("1000.00")

    @patch("app.services.finance.ap.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_credit_note(
        self, mock_tax_service, mock_db, organization_id, mock_supplier
    ):
        """Test tax transaction for credit note (negative amounts)."""
        credit_note = MockSupplierInvoice(
            organization_id=organization_id,
            invoice_type=SupplierInvoiceType.CREDIT_NOTE,
        )
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_with_tax = MockSupplierInvoiceLine(
            invoice_id=credit_note.invoice_id,
            tax_code_id=uuid.uuid4(),
            line_amount=Decimal("500.00"),
            tax_amount=Decimal("50.00"),
        )

        mock_db.scalar.return_value = fiscal_period

        mock_tax_txn = MagicMock()
        mock_tax_txn.transaction_id = uuid.uuid4()
        mock_tax_service.create_from_invoice_line.return_value = mock_tax_txn

        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=credit_note,
            lines=[line_with_tax],
            supplier=mock_supplier,
            exchange_rate=Decimal("1.0"),
            is_credit_note=True,
        )

        assert len(result) == 1
        call_kwargs = mock_tax_service.create_from_invoice_line.call_args[1]
        # Credit note should have negative base amount
        assert call_kwargs["base_amount"] == Decimal("-500.00")

    @patch("app.services.finance.ap.posting.helpers.tax_transaction_service")
    def test_create_tax_transactions_exception_handling(
        self, mock_tax_service, mock_db, organization_id, mock_invoice, mock_supplier
    ):
        """Test that exceptions in tax creation don't fail posting."""
        fiscal_period = MockFiscalPeriod(organization_id=organization_id)
        line_with_tax = MockSupplierInvoiceLine(
            invoice_id=mock_invoice.invoice_id,
            tax_code_id=uuid.uuid4(),
            tax_amount=Decimal("100.00"),
        )

        mock_db.scalar.return_value = fiscal_period

        mock_tax_service.create_from_invoice_line.side_effect = Exception(
            "Tax service error"
        )

        # Should not raise, just return empty list
        result = create_tax_transactions(
            db=mock_db,
            organization_id=organization_id,
            invoice=mock_invoice,
            lines=[line_with_tax],
            supplier=mock_supplier,
            exchange_rate=Decimal("1.0"),
        )

        assert result == []


# ============ APPostingResult Tests ============


class TestAPPostingResult:
    """Tests for APPostingResult dataclass."""

    def test_result_success(self):
        """Test successful result creation."""
        journal_id = uuid.uuid4()
        batch_id = uuid.uuid4()

        result = APPostingResult(
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
        result = APPostingResult(success=False, message="Validation failed")

        assert result.success is False
        assert result.journal_entry_id is None
        assert result.posting_batch_id is None
        assert result.message == "Validation failed"

    def test_result_defaults(self):
        """Test default values."""
        result = APPostingResult(success=True)

        assert result.journal_entry_id is None
        assert result.posting_batch_id is None
        assert result.message == ""
