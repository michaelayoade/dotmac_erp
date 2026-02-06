"""
Tests for TAXPostingAdapter - GL posting for tax transactions.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.services.finance.tax.tax_posting_adapter import (
    TAXPostingAdapter,
    TAXPostingResult,
)


class MockTaxCode:
    """Mock TaxCode model."""

    def __init__(self, **kwargs):
        self.tax_code_id = kwargs.get("tax_code_id", uuid4())
        self.tax_name = kwargs.get("tax_name", "VAT 20%")
        self.tax_paid_account_id = kwargs.get("tax_paid_account_id", uuid4())
        self.tax_collected_account_id = kwargs.get("tax_collected_account_id", uuid4())
        self.tax_expense_account_id = kwargs.get("tax_expense_account_id", uuid4())


class MockTaxTransaction:
    """Mock TaxTransaction model."""

    def __init__(self, **kwargs):
        from app.models.finance.tax.tax_transaction import TaxTransactionType

        self.transaction_id = kwargs.get("transaction_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.tax_code_id = kwargs.get("tax_code_id", uuid4())
        self.transaction_type = kwargs.get("transaction_type", TaxTransactionType.INPUT)
        self.transaction_date = kwargs.get("transaction_date", date.today())
        self.functional_tax_amount = kwargs.get(
            "functional_tax_amount", Decimal("100.00")
        )
        self.recoverable_amount = kwargs.get("recoverable_amount", Decimal("100.00"))
        self.non_recoverable_amount = kwargs.get("non_recoverable_amount", Decimal("0"))
        self.currency_code = kwargs.get("currency_code", "USD")
        self.exchange_rate = kwargs.get("exchange_rate", Decimal("1.0"))
        self.source_document_reference = kwargs.get(
            "source_document_reference", "INV-001"
        )
        self.journal_entry_id = kwargs.get("journal_entry_id")


class MockTaxJurisdiction:
    """Mock TaxJurisdiction model."""

    def __init__(self, **kwargs):
        self.jurisdiction_id = kwargs.get("jurisdiction_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.jurisdiction_name = kwargs.get("jurisdiction_name", "US Federal")
        self.jurisdiction_code = kwargs.get("jurisdiction_code", "US-FED")
        self.currency_code = kwargs.get("currency_code", "USD")
        self.current_tax_expense_account_id = kwargs.get(
            "current_tax_expense_account_id", uuid4()
        )
        self.current_tax_payable_account_id = kwargs.get(
            "current_tax_payable_account_id", uuid4()
        )
        self.deferred_tax_asset_account_id = kwargs.get(
            "deferred_tax_asset_account_id", uuid4()
        )
        self.deferred_tax_liability_account_id = kwargs.get(
            "deferred_tax_liability_account_id", uuid4()
        )
        self.deferred_tax_expense_account_id = kwargs.get(
            "deferred_tax_expense_account_id", uuid4()
        )


class MockDeferredTaxMovement:
    """Mock DeferredTaxMovement model."""

    def __init__(self, **kwargs):
        self.movement_id = kwargs.get("movement_id", uuid4())
        self.basis_id = kwargs.get("basis_id", uuid4())
        self.deferred_tax_movement_pl = kwargs.get(
            "deferred_tax_movement_pl", Decimal("1000.00")
        )
        self.deferred_tax_movement_oci = kwargs.get(
            "deferred_tax_movement_oci", Decimal("0")
        )
        self.deferred_tax_movement_equity = kwargs.get(
            "deferred_tax_movement_equity", Decimal("0")
        )
        self.journal_entry_id = kwargs.get("journal_entry_id")


class MockDeferredTaxBasis:
    """Mock DeferredTaxBasis model."""

    def __init__(self, **kwargs):
        self.basis_id = kwargs.get("basis_id", uuid4())
        self.organization_id = kwargs.get("organization_id", uuid4())
        self.jurisdiction_id = kwargs.get("jurisdiction_id", uuid4())
        self.basis_name = kwargs.get("basis_name", "Property Plant Equipment")
        self.basis_code = kwargs.get("basis_code", "PPE")
        self.is_asset = kwargs.get("is_asset", True)


class MockJournalResult:
    """Mock journal creation result."""

    def __init__(self):
        self.journal_entry_id = uuid4()


class MockPostingResult:
    """Mock posting result."""

    def __init__(self, success=True, message=""):
        self.success = success
        self.message = message
        self.posting_batch_id = uuid4() if success else None


class TestTAXPostingAdapterPostTaxTransaction:
    """Tests for post_tax_transaction method."""

    def test_post_input_tax_success(self, mock_db):
        """Test posting input tax transaction."""
        from app.models.finance.tax.tax_transaction import TaxTransactionType

        org_id = uuid4()
        txn_id = uuid4()
        user_id = uuid4()

        mock_txn = MockTaxTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TaxTransactionType.INPUT,
            recoverable_amount=Decimal("100.00"),
            non_recoverable_amount=Decimal("20.00"),
        )
        mock_code = MockTaxCode()

        mock_db.get.side_effect = [mock_txn, mock_code]

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_tax_transaction(
                db=mock_db,
                organization_id=org_id,
                transaction_id=txn_id,
                posting_date=date.today(),
                posted_by_user_id=user_id,
            )

            assert result.success is True
            assert result.journal_entry_id is not None
            assert "successfully" in result.message

    def test_post_output_tax_success(self, mock_db):
        """Test posting output tax transaction."""
        from app.models.finance.tax.tax_transaction import TaxTransactionType

        org_id = uuid4()
        txn_id = uuid4()
        user_id = uuid4()

        mock_txn = MockTaxTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TaxTransactionType.OUTPUT,
            functional_tax_amount=Decimal("200.00"),
        )
        mock_code = MockTaxCode()

        mock_db.get.side_effect = [mock_txn, mock_code]

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_tax_transaction(
                db=mock_db,
                organization_id=org_id,
                transaction_id=txn_id,
                posting_date=date.today(),
                posted_by_user_id=user_id,
            )

            assert result.success is True

    def test_post_withholding_tax_success(self, mock_db):
        """Test posting withholding tax transaction."""
        from app.models.finance.tax.tax_transaction import TaxTransactionType

        org_id = uuid4()
        txn_id = uuid4()
        user_id = uuid4()

        mock_txn = MockTaxTransaction(
            transaction_id=txn_id,
            organization_id=org_id,
            transaction_type=TaxTransactionType.WITHHOLDING,
            functional_tax_amount=Decimal("50.00"),
        )
        mock_code = MockTaxCode()

        mock_db.get.side_effect = [mock_txn, mock_code]

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_tax_transaction(
                db=mock_db,
                organization_id=org_id,
                transaction_id=txn_id,
                posting_date=date.today(),
                posted_by_user_id=user_id,
            )

            assert result.success is True

    def test_post_transaction_not_found(self, mock_db):
        """Test posting non-existent transaction."""
        mock_db.get.return_value = None

        result = TAXPostingAdapter.post_tax_transaction(
            db=mock_db,
            organization_id=uuid4(),
            transaction_id=uuid4(),
            posting_date=date.today(),
            posted_by_user_id=uuid4(),
        )

        assert result.success is False
        assert "not found" in result.message

    def test_post_tax_code_not_found(self, mock_db):
        """Test posting with missing tax code."""
        org_id = uuid4()
        mock_txn = MockTaxTransaction(organization_id=org_id)

        mock_db.get.side_effect = [mock_txn, None]

        result = TAXPostingAdapter.post_tax_transaction(
            db=mock_db,
            organization_id=org_id,
            transaction_id=uuid4(),
            posting_date=date.today(),
            posted_by_user_id=uuid4(),
        )

        assert result.success is False
        assert "Tax code not found" in result.message

    def test_post_journal_creation_fails(self, mock_db):
        """Test handling journal creation failure."""
        from app.models.finance.tax.tax_transaction import TaxTransactionType

        org_id = uuid4()
        mock_txn = MockTaxTransaction(
            organization_id=org_id,
            transaction_type=TaxTransactionType.OUTPUT,
        )
        mock_code = MockTaxCode()

        mock_db.get.side_effect = [mock_txn, mock_code]

        with patch(
            "app.services.finance.tax.tax_posting_adapter.JournalService"
        ) as mock_journal_svc:
            mock_journal_svc.create_journal.side_effect = HTTPException(
                status_code=400, detail="Journal validation failed"
            )

            result = TAXPostingAdapter.post_tax_transaction(
                db=mock_db,
                organization_id=org_id,
                transaction_id=uuid4(),
                posting_date=date.today(),
                posted_by_user_id=uuid4(),
            )

            assert result.success is False
            assert "Journal creation failed" in result.message

    def test_post_ledger_posting_fails(self, mock_db):
        """Test handling ledger posting failure."""
        from app.models.finance.tax.tax_transaction import TaxTransactionType

        org_id = uuid4()
        mock_txn = MockTaxTransaction(
            organization_id=org_id,
            transaction_type=TaxTransactionType.OUTPUT,
        )
        mock_code = MockTaxCode()

        mock_db.get.side_effect = [mock_txn, mock_code]

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=False, message="Period closed"
            )

            result = TAXPostingAdapter.post_tax_transaction(
                db=mock_db,
                organization_id=org_id,
                transaction_id=uuid4(),
                posting_date=date.today(),
                posted_by_user_id=uuid4(),
            )

            assert result.success is False
            assert "Ledger posting failed" in result.message


class TestTAXPostingAdapterPostCurrentTaxProvision:
    """Tests for post_current_tax_provision method."""

    def test_post_current_tax_expense_success(self, mock_db):
        """Test posting current tax expense."""
        org_id = uuid4()
        jur_id = uuid4()
        period_id = uuid4()
        user_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )
        mock_db.get.return_value = mock_jurisdiction

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_current_tax_provision(
                db=mock_db,
                organization_id=org_id,
                jurisdiction_id=jur_id,
                fiscal_period_id=period_id,
                current_tax_expense=Decimal("50000.00"),
                posting_date=date.today(),
                posted_by_user_id=user_id,
            )

            assert result.success is True
            assert "successfully" in result.message

    def test_post_current_tax_benefit_negative_amount(self, mock_db):
        """Test posting negative tax (benefit/refund)."""
        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )
        mock_db.get.return_value = mock_jurisdiction

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_current_tax_provision(
                db=mock_db,
                organization_id=org_id,
                jurisdiction_id=jur_id,
                fiscal_period_id=uuid4(),
                current_tax_expense=Decimal("-10000.00"),
                posting_date=date.today(),
                posted_by_user_id=uuid4(),
            )

            assert result.success is True

    def test_post_zero_tax_no_posting(self, mock_db):
        """Test zero tax amount doesn't create posting."""
        org_id = uuid4()
        jur_id = uuid4()

        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )
        mock_db.get.return_value = mock_jurisdiction

        result = TAXPostingAdapter.post_current_tax_provision(
            db=mock_db,
            organization_id=org_id,
            jurisdiction_id=jur_id,
            fiscal_period_id=uuid4(),
            current_tax_expense=Decimal("0"),
            posting_date=date.today(),
            posted_by_user_id=uuid4(),
        )

        assert result.success is True
        assert "No current tax to post" in result.message

    def test_post_jurisdiction_not_found(self, mock_db):
        """Test posting with missing jurisdiction."""
        mock_db.get.return_value = None

        result = TAXPostingAdapter.post_current_tax_provision(
            db=mock_db,
            organization_id=uuid4(),
            jurisdiction_id=uuid4(),
            fiscal_period_id=uuid4(),
            current_tax_expense=Decimal("10000.00"),
            posting_date=date.today(),
            posted_by_user_id=uuid4(),
        )

        assert result.success is False
        assert "Jurisdiction not found" in result.message


class TestTAXPostingAdapterPostDeferredTaxMovement:
    """Tests for post_deferred_tax_movement method."""

    def test_post_dta_increase_success(self, mock_db):
        """Test posting DTA increase."""
        org_id = uuid4()
        mov_id = uuid4()
        jur_id = uuid4()

        mock_movement = MockDeferredTaxMovement(
            movement_id=mov_id,
            deferred_tax_movement_pl=Decimal("5000.00"),
        )
        mock_basis = MockDeferredTaxBasis(
            organization_id=org_id, jurisdiction_id=jur_id, is_asset=True
        )
        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.get.side_effect = [mock_movement, mock_basis, mock_jurisdiction]

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_deferred_tax_movement(
                db=mock_db,
                organization_id=org_id,
                movement_id=mov_id,
                posting_date=date.today(),
                posted_by_user_id=uuid4(),
            )

            assert result.success is True

    def test_post_dtl_increase_success(self, mock_db):
        """Test posting DTL increase."""
        org_id = uuid4()
        mov_id = uuid4()
        jur_id = uuid4()

        mock_movement = MockDeferredTaxMovement(
            movement_id=mov_id,
            deferred_tax_movement_pl=Decimal("3000.00"),
        )
        mock_basis = MockDeferredTaxBasis(
            organization_id=org_id, jurisdiction_id=jur_id, is_asset=False
        )
        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.get.side_effect = [mock_movement, mock_basis, mock_jurisdiction]

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_deferred_tax_movement(
                db=mock_db,
                organization_id=org_id,
                movement_id=mov_id,
                posting_date=date.today(),
                posted_by_user_id=uuid4(),
            )

            assert result.success is True

    def test_post_dta_decrease(self, mock_db):
        """Test posting DTA decrease (negative movement)."""
        org_id = uuid4()
        mov_id = uuid4()
        jur_id = uuid4()

        mock_movement = MockDeferredTaxMovement(
            movement_id=mov_id,
            deferred_tax_movement_pl=Decimal("-2000.00"),
        )
        mock_basis = MockDeferredTaxBasis(
            organization_id=org_id, jurisdiction_id=jur_id, is_asset=True
        )
        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.get.side_effect = [mock_movement, mock_basis, mock_jurisdiction]

        with (
            patch(
                "app.services.finance.tax.tax_posting_adapter.JournalService"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.tax.tax_posting_adapter.LedgerPostingService"
            ) as mock_posting_svc,
        ):
            mock_journal_svc.create_journal.return_value = MockJournalResult()
            mock_posting_svc.post_journal_entry.return_value = MockPostingResult(
                success=True
            )

            result = TAXPostingAdapter.post_deferred_tax_movement(
                db=mock_db,
                organization_id=org_id,
                movement_id=mov_id,
                posting_date=date.today(),
                posted_by_user_id=uuid4(),
            )

            assert result.success is True

    def test_post_zero_movement_no_posting(self, mock_db):
        """Test zero movement doesn't create posting."""
        org_id = uuid4()
        mov_id = uuid4()
        jur_id = uuid4()

        mock_movement = MockDeferredTaxMovement(
            movement_id=mov_id,
            deferred_tax_movement_pl=Decimal("0"),
            deferred_tax_movement_oci=Decimal("0"),
            deferred_tax_movement_equity=Decimal("0"),
        )
        mock_basis = MockDeferredTaxBasis(
            organization_id=org_id, jurisdiction_id=jur_id
        )
        mock_jurisdiction = MockTaxJurisdiction(
            jurisdiction_id=jur_id, organization_id=org_id
        )

        mock_db.get.side_effect = [mock_movement, mock_basis, mock_jurisdiction]

        result = TAXPostingAdapter.post_deferred_tax_movement(
            db=mock_db,
            organization_id=org_id,
            movement_id=mov_id,
            posting_date=date.today(),
            posted_by_user_id=uuid4(),
        )

        assert result.success is True
        assert "No deferred tax movement to post" in result.message

    def test_post_movement_not_found(self, mock_db):
        """Test posting non-existent movement."""
        mock_db.get.return_value = None

        result = TAXPostingAdapter.post_deferred_tax_movement(
            db=mock_db,
            organization_id=uuid4(),
            movement_id=uuid4(),
            posting_date=date.today(),
            posted_by_user_id=uuid4(),
        )

        assert result.success is False
        assert "not found" in result.message

    def test_post_basis_not_found(self, mock_db):
        """Test posting with missing basis."""
        mock_movement = MockDeferredTaxMovement()
        mock_db.get.side_effect = [mock_movement, None]

        result = TAXPostingAdapter.post_deferred_tax_movement(
            db=mock_db,
            organization_id=uuid4(),
            movement_id=uuid4(),
            posting_date=date.today(),
            posted_by_user_id=uuid4(),
        )

        assert result.success is False
        assert "basis not found" in result.message


class TestTAXPostingResult:
    """Tests for TAXPostingResult dataclass."""

    def test_create_success_result(self):
        """Test creating successful result."""
        journal_id = uuid4()
        batch_id = uuid4()

        result = TAXPostingResult(
            success=True,
            journal_entry_id=journal_id,
            posting_batch_id=batch_id,
            message="Posted successfully",
        )

        assert result.success is True
        assert result.journal_entry_id == journal_id
        assert result.posting_batch_id == batch_id
        assert result.message == "Posted successfully"

    def test_create_failure_result(self):
        """Test creating failure result."""
        result = TAXPostingResult(success=False, message="Posting failed")

        assert result.success is False
        assert result.journal_entry_id is None
        assert result.posting_batch_id is None
        assert result.message == "Posting failed"
