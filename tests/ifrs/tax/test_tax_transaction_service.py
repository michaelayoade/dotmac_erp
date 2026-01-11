"""
Tests for TaxTransactionService.

Tests CRUD operations, invoice line tax creation, and VAT register/liability reports.
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.ifrs.tax.tax_transaction import TaxTransactionType
from app.services.ifrs.tax.tax_transaction import (
    TaxTransactionService,
    TaxTransactionInput,
    TaxReturnSummary,
    TaxByCodeSummary,
)


class MockTaxCode:
    """Mock TaxCode model for testing."""

    def __init__(
        self,
        tax_code_id=None,
        organization_id=None,
        tax_code="VAT20",
        tax_name="VAT 20%",
        tax_rate=Decimal("0.20"),
        jurisdiction_id=None,
        is_recoverable=True,
        recovery_rate=Decimal("1.0"),
        tax_return_box="Box1",
    ):
        self.tax_code_id = tax_code_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.tax_code = tax_code
        self.tax_name = tax_name
        self.tax_rate = tax_rate
        self.jurisdiction_id = jurisdiction_id or uuid4()
        self.is_recoverable = is_recoverable
        self.recovery_rate = recovery_rate
        self.tax_return_box = tax_return_box


class MockTaxTransaction:
    """Mock TaxTransaction model for testing."""

    def __init__(
        self,
        transaction_id=None,
        organization_id=None,
        fiscal_period_id=None,
        tax_code_id=None,
        jurisdiction_id=None,
        transaction_type=TaxTransactionType.OUTPUT,
        transaction_date=None,
        source_document_type="AR_INVOICE",
        source_document_id=None,
        source_document_line_id=None,
        source_document_reference="INV-001",
        counterparty_type="CUSTOMER",
        counterparty_id=None,
        counterparty_name="Test Customer",
        counterparty_tax_id="123456789",
        currency_code="USD",
        base_amount=Decimal("1000.00"),
        tax_rate=Decimal("0.20"),
        tax_amount=Decimal("200.00"),
        exchange_rate=Decimal("1.0"),
        functional_base_amount=Decimal("1000.00"),
        functional_tax_amount=Decimal("200.00"),
        recoverable_amount=Decimal("0"),
        non_recoverable_amount=Decimal("0"),
        tax_return_period=None,
        tax_return_box="Box1",
        is_included_in_return=False,
    ):
        self.transaction_id = transaction_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.tax_code_id = tax_code_id or uuid4()
        self.jurisdiction_id = jurisdiction_id or uuid4()
        self.transaction_type = transaction_type
        self.transaction_date = transaction_date or date.today()
        self.source_document_type = source_document_type
        self.source_document_id = source_document_id or uuid4()
        self.source_document_line_id = source_document_line_id
        self.source_document_reference = source_document_reference
        self.counterparty_type = counterparty_type
        self.counterparty_id = counterparty_id
        self.counterparty_name = counterparty_name
        self.counterparty_tax_id = counterparty_tax_id
        self.currency_code = currency_code
        self.base_amount = base_amount
        self.tax_rate = tax_rate
        self.tax_amount = tax_amount
        self.exchange_rate = exchange_rate
        self.functional_base_amount = functional_base_amount
        self.functional_tax_amount = functional_tax_amount
        self.recoverable_amount = recoverable_amount
        self.non_recoverable_amount = non_recoverable_amount
        self.tax_return_period = tax_return_period
        self.tax_return_box = tax_return_box
        self.is_included_in_return = is_included_in_return


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def mock_tax_code(org_id):
    return MockTaxCode(organization_id=org_id)


class TestCreateTransaction:
    """Tests for create_transaction method."""

    def test_create_transaction_success(self, mock_db, org_id, mock_tax_code):
        """Test successful tax transaction creation."""
        mock_db.get.return_value = mock_tax_code

        input_data = TaxTransactionInput(
            fiscal_period_id=uuid4(),
            tax_code_id=mock_tax_code.tax_code_id,
            jurisdiction_id=mock_tax_code.jurisdiction_id,
            transaction_type=TaxTransactionType.OUTPUT,
            transaction_date=date.today(),
            source_document_type="AR_INVOICE",
            source_document_id=uuid4(),
            currency_code="USD",
            base_amount=Decimal("1000.00"),
            tax_rate=Decimal("0.20"),
            tax_amount=Decimal("200.00"),
            functional_base_amount=Decimal("1000.00"),
            functional_tax_amount=Decimal("200.00"),
        )

        result = TaxTransactionService.create_transaction(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_transaction_invalid_tax_code(self, mock_db, org_id):
        """Test that invalid tax code raises error."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        input_data = TaxTransactionInput(
            fiscal_period_id=uuid4(),
            tax_code_id=uuid4(),
            jurisdiction_id=uuid4(),
            transaction_type=TaxTransactionType.OUTPUT,
            transaction_date=date.today(),
            source_document_type="AR_INVOICE",
            source_document_id=uuid4(),
            currency_code="USD",
            base_amount=Decimal("1000.00"),
            tax_rate=Decimal("0.20"),
            tax_amount=Decimal("200.00"),
            functional_base_amount=Decimal("1000.00"),
            functional_tax_amount=Decimal("200.00"),
        )

        with pytest.raises(HTTPException) as exc:
            TaxTransactionService.create_transaction(mock_db, org_id, input_data)

        assert exc.value.status_code == 404
        assert "Tax code not found" in exc.value.detail

    def test_create_transaction_wrong_organization(self, mock_db, org_id, mock_tax_code):
        """Test that tax code from different org raises error."""
        from fastapi import HTTPException

        mock_tax_code.organization_id = uuid4()  # Different org
        mock_db.get.return_value = mock_tax_code

        input_data = TaxTransactionInput(
            fiscal_period_id=uuid4(),
            tax_code_id=mock_tax_code.tax_code_id,
            jurisdiction_id=mock_tax_code.jurisdiction_id,
            transaction_type=TaxTransactionType.OUTPUT,
            transaction_date=date.today(),
            source_document_type="AR_INVOICE",
            source_document_id=uuid4(),
            currency_code="USD",
            base_amount=Decimal("1000.00"),
            tax_rate=Decimal("0.20"),
            tax_amount=Decimal("200.00"),
            functional_base_amount=Decimal("1000.00"),
            functional_tax_amount=Decimal("200.00"),
        )

        with pytest.raises(HTTPException) as exc:
            TaxTransactionService.create_transaction(mock_db, org_id, input_data)

        assert exc.value.status_code == 404


class TestCreateFromInvoiceLine:
    """Tests for create_from_invoice_line method."""

    def test_create_output_tax_from_sales_invoice(self, mock_db, org_id, mock_tax_code):
        """Test creating output tax from AR invoice line."""
        mock_db.get.return_value = mock_tax_code

        invoice_id = uuid4()
        invoice_line_id = uuid4()

        with patch.object(TaxTransactionService, 'create_transaction') as mock_create:
            mock_create.return_value = MockTaxTransaction()

            result = TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=mock_tax_code.tax_code_id,
                invoice_id=invoice_id,
                invoice_line_id=invoice_line_id,
                invoice_number="INV-001",
                transaction_date=date.today(),
                is_purchase=False,  # Sales = OUTPUT
                base_amount=Decimal("1000.00"),
                currency_code="USD",
                counterparty_name="Test Customer",
            )

            mock_create.assert_called_once()
            call_args = mock_create.call_args
            input_data = call_args[0][2]  # Third positional arg is input

            assert input_data.transaction_type == TaxTransactionType.OUTPUT
            assert input_data.source_document_type == "AR_INVOICE"
            assert input_data.counterparty_type == "CUSTOMER"

    def test_create_input_tax_from_purchase_invoice(self, mock_db, org_id, mock_tax_code):
        """Test creating input tax from AP invoice line."""
        mock_db.get.return_value = mock_tax_code

        invoice_id = uuid4()
        invoice_line_id = uuid4()

        with patch.object(TaxTransactionService, 'create_transaction') as mock_create:
            mock_create.return_value = MockTaxTransaction()

            result = TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=mock_tax_code.tax_code_id,
                invoice_id=invoice_id,
                invoice_line_id=invoice_line_id,
                invoice_number="PINV-001",
                transaction_date=date.today(),
                is_purchase=True,  # Purchase = INPUT
                base_amount=Decimal("500.00"),
                currency_code="USD",
                counterparty_name="Test Supplier",
            )

            mock_create.assert_called_once()
            call_args = mock_create.call_args
            input_data = call_args[0][2]

            assert input_data.transaction_type == TaxTransactionType.INPUT
            assert input_data.source_document_type == "AP_INVOICE"
            assert input_data.counterparty_type == "SUPPLIER"

    def test_tax_calculation_from_base_amount(self, mock_db, org_id, mock_tax_code):
        """Test that tax is correctly calculated from base amount."""
        mock_tax_code.tax_rate = Decimal("0.15")
        mock_db.get.return_value = mock_tax_code

        with patch.object(TaxTransactionService, 'create_transaction') as mock_create:
            mock_create.return_value = MockTaxTransaction()

            TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=mock_tax_code.tax_code_id,
                invoice_id=uuid4(),
                invoice_line_id=uuid4(),
                invoice_number="INV-001",
                transaction_date=date.today(),
                is_purchase=False,
                base_amount=Decimal("1000.00"),
                currency_code="USD",
            )

            input_data = mock_create.call_args[0][2]
            # 1000 * 0.15 = 150
            assert input_data.tax_amount == Decimal("150.00")
            assert input_data.tax_rate == Decimal("0.15")

    def test_recoverable_input_tax(self, mock_db, org_id, mock_tax_code):
        """Test recoverable input tax calculation."""
        mock_tax_code.is_recoverable = True
        mock_tax_code.recovery_rate = Decimal("0.80")  # 80% recoverable
        mock_db.get.return_value = mock_tax_code

        with patch.object(TaxTransactionService, 'create_transaction') as mock_create:
            mock_create.return_value = MockTaxTransaction()

            TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=mock_tax_code.tax_code_id,
                invoice_id=uuid4(),
                invoice_line_id=uuid4(),
                invoice_number="PINV-001",
                transaction_date=date.today(),
                is_purchase=True,
                base_amount=Decimal("1000.00"),
                currency_code="USD",
            )

            input_data = mock_create.call_args[0][2]
            # Tax = 1000 * 0.20 = 200
            # Recoverable = 200 * 0.80 = 160
            # Non-recoverable = 200 - 160 = 40
            assert input_data.recoverable_amount == Decimal("160.00")
            assert input_data.non_recoverable_amount == Decimal("40.00")

    def test_non_recoverable_input_tax(self, mock_db, org_id, mock_tax_code):
        """Test non-recoverable input tax calculation."""
        mock_tax_code.is_recoverable = False
        mock_db.get.return_value = mock_tax_code

        with patch.object(TaxTransactionService, 'create_transaction') as mock_create:
            mock_create.return_value = MockTaxTransaction()

            TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=mock_tax_code.tax_code_id,
                invoice_id=uuid4(),
                invoice_line_id=uuid4(),
                invoice_number="PINV-001",
                transaction_date=date.today(),
                is_purchase=True,
                base_amount=Decimal("1000.00"),
                currency_code="USD",
            )

            input_data = mock_create.call_args[0][2]
            # All tax is non-recoverable
            assert input_data.recoverable_amount == Decimal("0")
            assert input_data.non_recoverable_amount == Decimal("200.00")

    def test_output_tax_no_recovery(self, mock_db, org_id, mock_tax_code):
        """Test that output tax has no recovery amounts."""
        mock_db.get.return_value = mock_tax_code

        with patch.object(TaxTransactionService, 'create_transaction') as mock_create:
            mock_create.return_value = MockTaxTransaction()

            TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=mock_tax_code.tax_code_id,
                invoice_id=uuid4(),
                invoice_line_id=uuid4(),
                invoice_number="INV-001",
                transaction_date=date.today(),
                is_purchase=False,  # Output tax
                base_amount=Decimal("1000.00"),
                currency_code="USD",
            )

            input_data = mock_create.call_args[0][2]
            assert input_data.recoverable_amount == Decimal("0")
            assert input_data.non_recoverable_amount == Decimal("0")

    def test_exchange_rate_conversion(self, mock_db, org_id, mock_tax_code):
        """Test functional currency conversion with exchange rate."""
        mock_db.get.return_value = mock_tax_code

        with patch.object(TaxTransactionService, 'create_transaction') as mock_create:
            mock_create.return_value = MockTaxTransaction()

            TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=mock_tax_code.tax_code_id,
                invoice_id=uuid4(),
                invoice_line_id=uuid4(),
                invoice_number="INV-001",
                transaction_date=date.today(),
                is_purchase=False,
                base_amount=Decimal("1000.00"),
                currency_code="EUR",
                exchange_rate=Decimal("1.10"),  # 1 EUR = 1.10 USD
            )

            input_data = mock_create.call_args[0][2]
            # Base: 1000 * 1.10 = 1100
            # Tax: 200 * 1.10 = 220
            assert input_data.functional_base_amount == Decimal("1100.00")
            assert input_data.functional_tax_amount == Decimal("220.00")

    def test_invalid_tax_code(self, mock_db, org_id):
        """Test that invalid tax code raises error."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            TaxTransactionService.create_from_invoice_line(
                db=mock_db,
                organization_id=org_id,
                fiscal_period_id=uuid4(),
                tax_code_id=uuid4(),
                invoice_id=uuid4(),
                invoice_line_id=uuid4(),
                invoice_number="INV-001",
                transaction_date=date.today(),
                is_purchase=False,
                base_amount=Decimal("1000.00"),
                currency_code="USD",
            )

        assert exc.value.status_code == 404


class TestMarkIncludedInReturn:
    """Tests for mark_included_in_return method."""

    def test_mark_transactions_success(self, mock_db, org_id):
        """Test marking transactions as included in return."""
        txn1 = MockTaxTransaction(organization_id=org_id, is_included_in_return=False)
        txn2 = MockTaxTransaction(organization_id=org_id, is_included_in_return=False)

        mock_db.get.side_effect = [txn1, txn2]

        result = TaxTransactionService.mark_included_in_return(
            mock_db, org_id, [txn1.transaction_id, txn2.transaction_id], "2024Q1"
        )

        assert result == 2
        assert txn1.is_included_in_return is True
        assert txn1.tax_return_period == "2024Q1"
        assert txn2.is_included_in_return is True
        mock_db.commit.assert_called_once()

    def test_mark_skips_different_org(self, mock_db, org_id):
        """Test that transactions from different org are skipped."""
        txn = MockTaxTransaction(organization_id=uuid4())  # Different org
        mock_db.get.return_value = txn

        result = TaxTransactionService.mark_included_in_return(
            mock_db, org_id, [txn.transaction_id], "2024Q1"
        )

        assert result == 0
        assert txn.is_included_in_return is False

    def test_mark_skips_nonexistent(self, mock_db, org_id):
        """Test that nonexistent transactions are skipped."""
        mock_db.get.return_value = None

        result = TaxTransactionService.mark_included_in_return(
            mock_db, org_id, [uuid4()], "2024Q1"
        )

        assert result == 0


class TestGetReturnSummary:
    """Tests for get_return_summary method."""

    def test_return_summary_calculation(self, mock_db, org_id):
        """Test tax return summary calculation."""
        fiscal_period_id = uuid4()

        # Mock query results
        mock_db.query.return_value.filter.return_value.scalar.side_effect = [
            Decimal("5000.00"),  # output_tax
            Decimal("2000.00"),  # input_recoverable
            Decimal("500.00"),   # input_non_recoverable
            Decimal("300.00"),   # withholding_tax
            10,                  # transaction_count
        ]

        result = TaxTransactionService.get_return_summary(
            mock_db, org_id, fiscal_period_id
        )

        assert isinstance(result, TaxReturnSummary)
        assert result.output_tax == Decimal("5000.00")
        assert result.input_tax_recoverable == Decimal("2000.00")
        assert result.input_tax_non_recoverable == Decimal("500.00")
        assert result.withholding_tax == Decimal("300.00")
        assert result.net_payable == Decimal("3000.00")  # 5000 - 2000
        assert result.transaction_count == 10

    def test_return_summary_zero_values(self, mock_db, org_id):
        """Test return summary with no transactions."""
        mock_db.query.return_value.filter.return_value.scalar.return_value = None

        result = TaxTransactionService.get_return_summary(
            mock_db, org_id, uuid4()
        )

        assert result.output_tax == Decimal("0")
        assert result.input_tax_recoverable == Decimal("0")
        assert result.net_payable == Decimal("0")


class TestGetTransaction:
    """Tests for get method."""

    def test_get_existing_transaction(self, mock_db):
        """Test getting existing transaction."""
        txn = MockTaxTransaction()
        mock_db.get.return_value = txn

        result = TaxTransactionService.get(mock_db, str(txn.transaction_id))

        assert result == txn

    def test_get_nonexistent_raises_error(self, mock_db):
        """Test that getting nonexistent transaction raises error."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            TaxTransactionService.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListTransactions:
    """Tests for list method."""

    def test_list_all_transactions(self, mock_db, org_id):
        """Test listing all transactions."""
        transactions = [MockTaxTransaction() for _ in range(3)]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = transactions
        mock_db.query.return_value = mock_query

        result = TaxTransactionService.list(
            mock_db, organization_id=str(org_id)
        )

        assert len(result) == 3

    def test_list_with_date_filter(self, mock_db, org_id):
        """Test listing with date filters."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = []
        mock_db.query.return_value = mock_query

        result = TaxTransactionService.list(
            mock_db,
            organization_id=str(org_id),
            start_date=date.today() - timedelta(days=30),
            end_date=date.today(),
        )

        assert result == []

    def test_list_with_type_filter(self, mock_db, org_id):
        """Test listing with transaction type filter."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = []
        mock_db.query.return_value = mock_query

        result = TaxTransactionService.list(
            mock_db,
            organization_id=str(org_id),
            transaction_type=TaxTransactionType.OUTPUT,
        )

        assert result == []


class TestGetUnreportedTransactions:
    """Tests for get_unreported_transactions method."""

    def test_get_unreported_transactions(self, mock_db, org_id):
        """Test getting unreported transactions."""
        transactions = [
            MockTaxTransaction(is_included_in_return=False),
            MockTaxTransaction(is_included_in_return=False),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = transactions
        mock_db.query.return_value = mock_query

        result = TaxTransactionService.get_unreported_transactions(
            mock_db, str(org_id), str(uuid4())
        )

        assert len(result) == 2


class TestGetVatRegister:
    """Tests for get_vat_register method."""

    def test_get_vat_register_success(self, mock_db, org_id):
        """Test getting VAT register."""
        tax_code = MockTaxCode()
        transaction = MockTaxTransaction()

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 1
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [(transaction, tax_code)]
        mock_db.query.return_value = mock_query

        transactions, total = TaxTransactionService.get_vat_register(
            mock_db,
            str(org_id),
            start_date=date.today() - timedelta(days=30),
            end_date=date.today(),
        )

        assert total == 1
        assert len(transactions) == 1
        assert transactions[0]["transaction_id"] == str(transaction.transaction_id)


class TestGetTaxLiabilitySummary:
    """Tests for get_tax_liability_summary method."""

    def test_liability_summary_default(self, mock_db, org_id):
        """Test default liability summary (overall period)."""
        mock_db.query.return_value.filter.return_value.scalar.side_effect = [
            Decimal("5000.00"),  # output
            Decimal("2000.00"),  # input
            Decimal("1800.00"),  # recoverable
        ]

        result = TaxTransactionService.get_tax_liability_summary(
            mock_db,
            str(org_id),
            start_date=date.today() - timedelta(days=30),
            end_date=date.today(),
            group_by="period",
        )

        assert len(result) == 1
        assert result[0]["output_tax"] == "5000.00"
        assert result[0]["input_tax"] == "2000.00"
        assert result[0]["input_tax_recoverable"] == "1800.00"
        assert result[0]["net_payable"] == "3200.00"  # 5000 - 1800
