"""
Tests for INVPostingAdapter.

Tests posting inventory transactions (receipts, issues, adjustments) to the GL.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch


from app.services.inventory.inv_posting_adapter import (
    INVPostingAdapter,
    INVPostingResult,
)
from app.models.inventory.inventory_transaction import TransactionType


class MockItem:
    """Mock Item model."""

    def __init__(
        self,
        item_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_code: str = "TEST-001",
        category_id: uuid.UUID = None,
        inventory_account_id: uuid.UUID = None,
        cogs_account_id: uuid.UUID = None,
    ):
        self.item_id = item_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.item_code = item_code
        self.category_id = category_id or uuid.uuid4()
        self.inventory_account_id = inventory_account_id or uuid.uuid4()
        self.cogs_account_id = cogs_account_id or uuid.uuid4()


class MockItemCategory:
    """Mock ItemCategory model."""

    def __init__(
        self,
        category_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        category_code: str = "CAT-001",
        inventory_account_id: uuid.UUID = None,
        cogs_account_id: uuid.UUID = None,
        inventory_adjustment_account_id: uuid.UUID = None,
        purchase_variance_account_id: uuid.UUID = None,
    ):
        self.category_id = category_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.category_code = category_code
        self.inventory_account_id = inventory_account_id or uuid.uuid4()
        self.cogs_account_id = cogs_account_id or uuid.uuid4()
        self.inventory_adjustment_account_id = (
            inventory_adjustment_account_id or uuid.uuid4()
        )
        self.purchase_variance_account_id = purchase_variance_account_id or uuid.uuid4()


class MockInventoryTransaction:
    """Mock InventoryTransaction model."""

    def __init__(
        self,
        transaction_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        transaction_type: TransactionType = TransactionType.RECEIPT,
        transaction_date: datetime = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        quantity: Decimal = Decimal("10.00"),
        unit_cost: Decimal = Decimal("10.00"),
        total_cost: Decimal = None,
        cost_variance: Decimal = Decimal("0"),
        currency_code: str = "USD",
        reference: str = None,
        journal_entry_id: uuid.UUID = None,
    ):
        self.transaction_id = transaction_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.transaction_type = transaction_type
        self.transaction_date = transaction_date or datetime.now(timezone.utc)
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.quantity = quantity
        self.unit_cost = unit_cost
        self.total_cost = total_cost or (quantity * unit_cost)
        self.cost_variance = cost_variance
        self.currency_code = currency_code
        self.reference = reference
        self.journal_entry_id = journal_entry_id


class MockJournalEntry:
    """Mock JournalEntry model."""

    def __init__(
        self,
        journal_entry_id: uuid.UUID = None,
    ):
        self.journal_entry_id = journal_entry_id or uuid.uuid4()


class MockPostingResult:
    """Mock posting result."""

    def __init__(
        self,
        success: bool = True,
        posting_batch_id: uuid.UUID = None,
        message: str = "",
    ):
        self.success = success
        self.posting_batch_id = posting_batch_id or uuid.uuid4()
        self.message = message


class TestPostReceipt:
    """Tests for post_receipt method."""

    @patch("app.services.inventory.posting.receipt.LedgerPostingService")
    @patch("app.services.inventory.posting.receipt.JournalService")
    def test_post_receipt_success(self, mock_journal_service, mock_ledger_posting):
        """Test successful receipt posting."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            total_cost=Decimal("1000.00"),
            journal_entry_id=None,
        )
        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            category_id=category_id,
        )
        mock_category = MockItemCategory(
            category_id=category_id,
            organization_id=org_id,
        )
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == mock_journal.journal_entry_id

    @patch("app.services.inventory.posting.receipt.LedgerPostingService")
    @patch("app.services.inventory.posting.receipt.JournalService")
    def test_post_receipt_with_ap_control_account(
        self, mock_journal_service, mock_ledger_posting
    ):
        """Test receipt posting with AP control account specified."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()
        ap_control_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(category_id=category_id)
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
            ap_control_account_id=ap_control_id,
        )

        assert result.success is True
        mock_journal_service.create_journal.assert_called_once()

    def test_post_receipt_transaction_not_found(self):
        """Test receipt posting fails when transaction not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_db.get.return_value = None

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Transaction not found" in result.message

    def test_post_receipt_wrong_transaction_type(self):
        """Test receipt posting fails for non-receipt transaction."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ISSUE,
            journal_entry_id=None,
        )

        mock_db.get.return_value = mock_transaction

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not a receipt" in result.message

    def test_post_receipt_already_posted(self):
        """Test receipt posting fails when already posted."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            journal_entry_id=uuid.uuid4(),
        )

        mock_db.get.return_value = mock_transaction

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "already posted" in result.message

    def test_post_receipt_item_not_found(self):
        """Test receipt posting fails when item not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            journal_entry_id=None,
        )

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            return None

        mock_db.get.side_effect = mock_get

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Item not found" in result.message

    def test_post_receipt_category_not_found(self):
        """Test receipt posting fails when category not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id)

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            return None

        mock_db.get.side_effect = mock_get

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Item category not found" in result.message

    @patch("app.services.inventory.posting.receipt.LedgerPostingService")
    @patch("app.services.inventory.posting.receipt.JournalService")
    def test_post_receipt_with_cost_variance(
        self, mock_journal_service, mock_ledger_posting
    ):
        """Test receipt posting with cost variance (standard costing)."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            total_cost=Decimal("1000.00"),
            cost_variance=Decimal("50.00"),
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(
            category_id=category_id,
            purchase_variance_account_id=uuid.uuid4(),
        )
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True

    @patch("app.services.inventory.posting.receipt.LedgerPostingService")
    @patch("app.services.inventory.posting.receipt.JournalService")
    def test_post_receipt_ledger_posting_fails(
        self, mock_journal_service, mock_ledger_posting
    ):
        """Test receipt posting when ledger posting fails."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(category_id=category_id)
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=False, message="Ledger error"
        )

        result = INVPostingAdapter.post_receipt(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Ledger posting failed" in result.message


class TestPostIssue:
    """Tests for post_issue method."""

    @patch("app.services.inventory.posting.issue.LedgerPostingService")
    @patch("app.services.inventory.posting.issue.JournalService")
    def test_post_issue_success(self, mock_journal_service, mock_ledger_posting):
        """Test successful issue posting."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            total_cost=Decimal("500.00"),
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(category_id=category_id)
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_issue(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True
        assert result.journal_entry_id == mock_journal.journal_entry_id

    @patch("app.services.inventory.posting.issue.LedgerPostingService")
    @patch("app.services.inventory.posting.issue.JournalService")
    def test_post_issue_with_expense_account(
        self, mock_journal_service, mock_ledger_posting
    ):
        """Test issue posting with override expense account."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()
        expense_account_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(category_id=category_id)
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_issue(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
            expense_account_id=expense_account_id,
        )

        assert result.success is True

    def test_post_issue_transaction_not_found(self):
        """Test issue posting fails when transaction not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_db.get.return_value = None

        result = INVPostingAdapter.post_issue(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Transaction not found" in result.message

    def test_post_issue_wrong_transaction_type(self):
        """Test issue posting fails for non-issue transaction."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            journal_entry_id=None,
        )

        mock_db.get.return_value = mock_transaction

        result = INVPostingAdapter.post_issue(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not an issue or sale" in result.message

    def test_post_issue_already_posted(self):
        """Test issue posting fails when already posted."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ISSUE,
            journal_entry_id=uuid.uuid4(),
        )

        mock_db.get.return_value = mock_transaction

        result = INVPostingAdapter.post_issue(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "already posted" in result.message


class TestPostAdjustment:
    """Tests for post_adjustment method."""

    @patch("app.services.inventory.posting.adjustment.LedgerPostingService")
    @patch("app.services.inventory.posting.adjustment.JournalService")
    def test_post_positive_adjustment_success(
        self, mock_journal_service, mock_ledger_posting
    ):
        """Test successful positive adjustment posting."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ADJUSTMENT,
            item_id=item_id,
            quantity=Decimal("10"),
            total_cost=Decimal("100.00"),
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(category_id=category_id)
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_adjustment(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True

    @patch("app.services.inventory.posting.adjustment.LedgerPostingService")
    @patch("app.services.inventory.posting.adjustment.JournalService")
    def test_post_negative_adjustment_success(
        self, mock_journal_service, mock_ledger_posting
    ):
        """Test successful negative adjustment posting."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ADJUSTMENT,
            item_id=item_id,
            quantity=Decimal("-10"),
            total_cost=Decimal("100.00"),
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(category_id=category_id)
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_adjustment(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True

    def test_post_adjustment_transaction_not_found(self):
        """Test adjustment posting fails when transaction not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_db.get.return_value = None

        result = INVPostingAdapter.post_adjustment(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Transaction not found" in result.message

    def test_post_adjustment_wrong_transaction_type(self):
        """Test adjustment posting fails for non-adjustment transaction."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
            journal_entry_id=None,
        )

        mock_db.get.return_value = mock_transaction

        result = INVPostingAdapter.post_adjustment(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not an adjustment" in result.message

    @patch("app.services.inventory.posting.adjustment.LedgerPostingService")
    @patch("app.services.inventory.posting.adjustment.JournalService")
    def test_post_scrap_adjustment(self, mock_journal_service, mock_ledger_posting):
        """Test posting scrap as adjustment."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()
        item_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.SCRAP,
            item_id=item_id,
            quantity=Decimal("-5"),
            total_cost=Decimal("50.00"),
            journal_entry_id=None,
        )
        mock_item = MockItem(item_id=item_id, category_id=category_id)
        mock_category = MockItemCategory(category_id=category_id)
        mock_journal = MockJournalEntry()

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "InventoryTransaction" in model_str:
                return mock_transaction
            elif "Item" in model_str and "ItemCategory" not in model_str:
                return mock_item
            elif "ItemCategory" in model_str:
                return mock_category
            return None

        mock_db.get.side_effect = mock_get
        mock_journal_service.create_journal.return_value = mock_journal
        mock_ledger_posting.post_journal_entry.return_value = MockPostingResult(
            success=True
        )

        result = INVPostingAdapter.post_adjustment(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is True


class TestPostTransaction:
    """Tests for post_transaction router method."""

    @patch("app.services.inventory.posting.router.post_receipt")
    def test_routes_receipt_to_post_receipt(self, mock_post_receipt):
        """Test routing RECEIPT to post_receipt."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RECEIPT,
        )
        mock_db.get.return_value = mock_transaction
        mock_post_receipt.return_value = INVPostingResult(success=True)

        INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        mock_post_receipt.assert_called_once()

    @patch("app.services.inventory.posting.router.post_receipt")
    def test_routes_return_to_post_receipt(self, mock_post_receipt):
        """Test routing RETURN to post_receipt."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.RETURN,
        )
        mock_db.get.return_value = mock_transaction
        mock_post_receipt.return_value = INVPostingResult(success=True)

        INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        mock_post_receipt.assert_called_once()

    @patch("app.services.inventory.posting.router.post_issue")
    def test_routes_issue_to_post_issue(self, mock_post_issue):
        """Test routing ISSUE to post_issue."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ISSUE,
        )
        mock_db.get.return_value = mock_transaction
        mock_post_issue.return_value = INVPostingResult(success=True)

        INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        mock_post_issue.assert_called_once()

    @patch("app.services.inventory.posting.router.post_issue")
    def test_routes_sale_to_post_issue(self, mock_post_issue):
        """Test routing SALE to post_issue."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.SALE,
        )
        mock_db.get.return_value = mock_transaction
        mock_post_issue.return_value = INVPostingResult(success=True)

        INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        mock_post_issue.assert_called_once()

    @patch("app.services.inventory.posting.router.post_adjustment")
    def test_routes_adjustment_to_post_adjustment(self, mock_post_adjustment):
        """Test routing ADJUSTMENT to post_adjustment."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.ADJUSTMENT,
        )
        mock_db.get.return_value = mock_transaction
        mock_post_adjustment.return_value = INVPostingResult(success=True)

        INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        mock_post_adjustment.assert_called_once()

    @patch("app.services.inventory.posting.router.post_adjustment")
    def test_routes_scrap_to_post_adjustment(self, mock_post_adjustment):
        """Test routing SCRAP to post_adjustment."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.SCRAP,
        )
        mock_db.get.return_value = mock_transaction
        mock_post_adjustment.return_value = INVPostingResult(success=True)

        INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        mock_post_adjustment.assert_called_once()

    def test_post_transaction_not_found(self):
        """Test post_transaction fails when transaction not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_db.get.return_value = None

        result = INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "Transaction not found" in result.message

    def test_post_transaction_unsupported_type(self):
        """Test post_transaction fails for unsupported type."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TransactionType.TRANSFER,
        )
        mock_db.get.return_value = mock_transaction

        result = INVPostingAdapter.post_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=txn_id,
            posting_date=date.today(),
            posted_by_user_id=user_id,
        )

        assert result.success is False
        assert "not supported" in result.message


class TestINVPostingResult:
    """Tests for INVPostingResult dataclass."""

    def test_default_values(self):
        """Test INVPostingResult default values."""
        result = INVPostingResult(success=True)

        assert result.success is True
        assert result.journal_entry_id is None
        assert result.posting_batch_id is None
        assert result.message == ""

    def test_with_all_values(self):
        """Test INVPostingResult with all values."""
        journal_id = uuid.uuid4()
        batch_id = uuid.uuid4()

        result = INVPostingResult(
            success=False,
            journal_entry_id=journal_id,
            posting_batch_id=batch_id,
            message="Error occurred",
        )

        assert result.success is False
        assert result.journal_entry_id == journal_id
        assert result.posting_batch_id == batch_id
        assert result.message == "Error occurred"
