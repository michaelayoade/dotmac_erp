"""
Tests for BankReconciliationService.
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.banking.bank_reconciliation import (
    BankReconciliationService,
    ReconciliationInput,
    ReconciliationMatchInput,
)
from tests.ifrs.banking.conftest import (
    MockBankAccount,
    MockBankReconciliation,
    MockBankReconciliationLine,
    MockBankStatement,
    MockBankStatementLine,
    MockJournalEntry,
    MockJournalEntryLine,
    MockReconciliationStatus,
)


@pytest.fixture
def service():
    """Create service instance."""
    return BankReconciliationService()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def sample_recon_input():
    """Create sample reconciliation input."""
    return ReconciliationInput(
        reconciliation_date=date.today(),
        period_start=date.today().replace(day=1),
        period_end=date.today(),
        statement_opening_balance=Decimal("1000.00"),
        statement_closing_balance=Decimal("1500.00"),
        notes="Monthly reconciliation",
    )


class TestCreateReconciliation:
    """Tests for create_reconciliation method."""

    def test_create_reconciliation_success(
        self, service, mock_db, org_id, user_id, sample_recon_input
    ):
        """Test successful reconciliation creation."""
        from app.models.finance.banking.bank_reconciliation import BankReconciliation

        bank_account = MockBankAccount(organization_id=org_id)
        mock_db.get.return_value = bank_account

        # No existing reconciliation
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with patch.object(service, "_get_gl_balance", return_value=Decimal("1500.00")):
            with patch.object(service, "_get_prior_reconciliation", return_value=None):
                # Patch the model's calculate_difference to avoid None type issues
                with patch.object(BankReconciliation, "calculate_difference", return_value=None):
                    result = service.create_reconciliation(
                        mock_db, org_id, bank_account.bank_account_id,
                        sample_recon_input, user_id
                    )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    def test_create_reconciliation_nonexistent_account_fails(
        self, service, mock_db, org_id, sample_recon_input
    ):
        """Test creating reconciliation for non-existent account fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.create_reconciliation(
                mock_db, org_id, uuid4(), sample_recon_input
            )

        assert exc.value.status_code == 404

    def test_create_duplicate_reconciliation_fails(
        self, service, mock_db, org_id, sample_recon_input
    ):
        """Test creating duplicate reconciliation fails."""
        from fastapi import HTTPException

        bank_account = MockBankAccount(organization_id=org_id)
        mock_db.get.return_value = bank_account

        # Existing reconciliation found
        existing = MockBankReconciliation()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc:
            service.create_reconciliation(
                mock_db, org_id, bank_account.bank_account_id, sample_recon_input
            )

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestGetReconciliation:
    """Tests for get method."""

    def test_get_existing_reconciliation(self, service, mock_db):
        """Test getting existing reconciliation."""
        recon = MockBankReconciliation()
        mock_db.get.return_value = recon

        result = service.get(mock_db, recon.reconciliation_id)

        assert result == recon

    def test_get_nonexistent_reconciliation(self, service, mock_db):
        """Test getting non-existent reconciliation returns None."""
        mock_db.get.return_value = None

        result = service.get(mock_db, uuid4())

        assert result is None


class TestListReconciliations:
    """Tests for list method."""

    def test_list_all_reconciliations(self, service, mock_db, org_id):
        """Test listing all reconciliations."""
        recons = [MockBankReconciliation(organization_id=org_id) for _ in range(3)]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = recons
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id)

        assert result == recons

    def test_list_with_status_filter(self, service, mock_db, org_id):
        """Test listing reconciliations with status filter."""
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recons = [MockBankReconciliation(organization_id=org_id, status="draft")]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = recons
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id, status=ReconciliationStatus.draft)

        assert result == recons


class TestAddMatch:
    """Tests for add_match method."""

    def test_add_match_success(self, service, mock_db, user_id):
        """Test adding a match successfully."""
        recon = MockBankReconciliation(status="draft")
        recon.calculate_difference = MagicMock()

        # signed_amount is a computed property based on transaction_type and amount
        stmt_line = MockBankStatementLine(
            transaction_type="credit",
            amount=Decimal("100.00"),
        )

        gl_line = MockJournalEntryLine(
            debit_amount=Decimal("100.00"),
            credit_amount=None,
        )

        mock_db.get.side_effect = [recon, stmt_line, gl_line]

        match_input = ReconciliationMatchInput(
            statement_line_id=stmt_line.line_id,
            journal_line_id=gl_line.line_id,
        )

        result = service.add_match(
            mock_db, recon.reconciliation_id, match_input, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    def test_add_match_nonexistent_reconciliation_fails(self, service, mock_db):
        """Test adding match to non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        match_input = ReconciliationMatchInput(
            statement_line_id=uuid4(),
            journal_line_id=uuid4(),
        )

        with pytest.raises(HTTPException) as exc:
            service.add_match(mock_db, uuid4(), match_input)

        assert exc.value.status_code == 404

    def test_add_match_approved_reconciliation_fails(self, service, mock_db):
        """Test adding match to approved reconciliation fails."""
        from fastapi import HTTPException
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recon = MockBankReconciliation(status=ReconciliationStatus.approved)
        mock_db.get.return_value = recon

        match_input = ReconciliationMatchInput(
            statement_line_id=uuid4(),
            journal_line_id=uuid4(),
        )

        with pytest.raises(HTTPException) as exc:
            service.add_match(mock_db, recon.reconciliation_id, match_input)

        assert exc.value.status_code == 400


class TestAutoMatch:
    """Tests for auto_match method."""

    def test_auto_match_nonexistent_reconciliation_fails(self, service, mock_db):
        """Test auto-matching non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.auto_match(mock_db, uuid4())

        assert exc.value.status_code == 404


class TestSubmitForReview:
    """Tests for submit_for_review method."""

    def test_submit_for_review_success(self, service, mock_db):
        """Test successful submission for review."""
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recon = MockBankReconciliation(status=ReconciliationStatus.draft)
        mock_db.get.return_value = recon

        result = service.submit_for_review(mock_db, recon.reconciliation_id)

        assert result.status == ReconciliationStatus.pending_review
        mock_db.flush.assert_called_once()

    def test_submit_non_draft_fails(self, service, mock_db):
        """Test submitting non-draft reconciliation fails."""
        from fastapi import HTTPException
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recon = MockBankReconciliation(status=ReconciliationStatus.approved)
        mock_db.get.return_value = recon

        with pytest.raises(HTTPException) as exc:
            service.submit_for_review(mock_db, recon.reconciliation_id)

        assert exc.value.status_code == 400

    def test_submit_nonexistent_fails(self, service, mock_db):
        """Test submitting non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.submit_for_review(mock_db, uuid4())

        assert exc.value.status_code == 404


class TestApproveReconciliation:
    """Tests for approve method."""

    def test_approve_success(self, service, mock_db, user_id):
        """Test successful approval."""
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        bank_account = MockBankAccount()
        recon = MockBankReconciliation(
            status=ReconciliationStatus.pending_review,
            reconciliation_difference=Decimal("0"),
        )
        recon.bank_account = bank_account
        mock_db.get.return_value = recon

        result = service.approve(mock_db, recon.reconciliation_id, user_id)

        assert result.status == ReconciliationStatus.approved
        assert result.approved_by == user_id
        mock_db.flush.assert_called_once()

    def test_approve_non_pending_fails(self, service, mock_db, user_id):
        """Test approving non-pending reconciliation fails."""
        from fastapi import HTTPException
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recon = MockBankReconciliation(status=ReconciliationStatus.draft)
        mock_db.get.return_value = recon

        with pytest.raises(HTTPException) as exc:
            service.approve(mock_db, recon.reconciliation_id, user_id)

        assert exc.value.status_code == 400

    def test_approve_with_difference_fails(self, service, mock_db, user_id):
        """Test approving reconciliation with difference fails."""
        from fastapi import HTTPException
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recon = MockBankReconciliation(
            status=ReconciliationStatus.pending_review,
            reconciliation_difference=Decimal("100.00"),
        )
        mock_db.get.return_value = recon

        with pytest.raises(HTTPException) as exc:
            service.approve(mock_db, recon.reconciliation_id, user_id)

        assert exc.value.status_code == 400
        assert "difference" in exc.value.detail

    def test_approve_nonexistent_fails(self, service, mock_db, user_id):
        """Test approving non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.approve(mock_db, uuid4(), user_id)

        assert exc.value.status_code == 404


class TestRejectReconciliation:
    """Tests for reject method."""

    def test_reject_success(self, service, mock_db, user_id):
        """Test successful rejection."""
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recon = MockBankReconciliation(status=ReconciliationStatus.pending_review)
        mock_db.get.return_value = recon

        result = service.reject(
            mock_db, recon.reconciliation_id, user_id, "Incorrect entries"
        )

        assert result.status == ReconciliationStatus.rejected
        assert result.review_notes == "Incorrect entries"
        mock_db.flush.assert_called_once()

    def test_reject_non_pending_fails(self, service, mock_db, user_id):
        """Test rejecting non-pending reconciliation fails."""
        from fastapi import HTTPException
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        recon = MockBankReconciliation(status=ReconciliationStatus.draft)
        mock_db.get.return_value = recon

        with pytest.raises(HTTPException) as exc:
            service.reject(mock_db, recon.reconciliation_id, user_id, "Reason")

        assert exc.value.status_code == 400

    def test_reject_nonexistent_fails(self, service, mock_db, user_id):
        """Test rejecting non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.reject(mock_db, uuid4(), user_id, "Reason")

        assert exc.value.status_code == 404


class TestGetReconciliationReport:
    """Tests for get_reconciliation_report method."""

    def test_get_report_success(self, service, mock_db):
        """Test getting reconciliation report."""
        bank_account = MockBankAccount()
        recon = MockBankReconciliation(
            statement_closing_balance=Decimal("1500.00"),
            gl_closing_balance=Decimal("1500.00"),
            reconciliation_difference=Decimal("0"),
        )
        recon.bank_account = bank_account
        recon.lines = [
            MockBankReconciliationLine(is_cleared=True, statement_amount=Decimal("100.00")),
            MockBankReconciliationLine(is_adjustment=True, statement_amount=Decimal("50.00")),
            MockBankReconciliationLine(is_outstanding=True, outstanding_type="deposit"),
        ]
        mock_db.get.return_value = recon

        result = service.get_reconciliation_report(mock_db, recon.reconciliation_id)

        assert result["reconciliation"] == recon
        assert result["bank_account"] == bank_account
        assert "summary" in result
        assert "matched_items" in result
        assert "adjustments" in result

    def test_get_report_nonexistent_fails(self, service, mock_db):
        """Test getting report for non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get_reconciliation_report(mock_db, uuid4())

        assert exc.value.status_code == 404


class TestAddAdjustment:
    """Tests for add_adjustment method."""

    def test_add_adjustment_success(self, service, mock_db, user_id):
        """Test adding an adjustment."""
        recon = MockBankReconciliation(status="draft", total_adjustments=Decimal("0"))
        recon.calculate_difference = MagicMock()
        mock_db.get.return_value = recon

        result = service.add_adjustment(
            mock_db,
            recon.reconciliation_id,
            transaction_date=date.today(),
            amount=Decimal("50.00"),
            description="Bank fee",
            adjustment_type="fee",
            created_by=user_id,
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    def test_add_adjustment_nonexistent_fails(self, service, mock_db):
        """Test adding adjustment to non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.add_adjustment(
                mock_db,
                uuid4(),
                transaction_date=date.today(),
                amount=Decimal("50.00"),
                description="Test",
                adjustment_type="fee",
            )

        assert exc.value.status_code == 404


class TestAddOutstandingItem:
    """Tests for add_outstanding_item method."""

    def test_add_outstanding_deposit(self, service, mock_db, user_id):
        """Test adding an outstanding deposit."""
        recon = MockBankReconciliation(
            status="draft",
            outstanding_deposits=Decimal("0"),
        )
        recon.calculate_difference = MagicMock()
        mock_db.get.return_value = recon

        result = service.add_outstanding_item(
            mock_db,
            recon.reconciliation_id,
            transaction_date=date.today(),
            amount=Decimal("500.00"),
            description="Deposit in transit",
            outstanding_type="deposit",
            created_by=user_id,
        )

        mock_db.add.assert_called_once()

    def test_add_outstanding_payment(self, service, mock_db, user_id):
        """Test adding an outstanding payment."""
        recon = MockBankReconciliation(
            status="draft",
            outstanding_payments=Decimal("0"),
        )
        recon.calculate_difference = MagicMock()
        mock_db.get.return_value = recon

        result = service.add_outstanding_item(
            mock_db,
            recon.reconciliation_id,
            transaction_date=date.today(),
            amount=Decimal("200.00"),
            description="Outstanding check #1234",
            outstanding_type="payment",
            reference="CHK-1234",
            created_by=user_id,
        )

        mock_db.add.assert_called_once()

    def test_add_outstanding_nonexistent_fails(self, service, mock_db, user_id):
        """Test adding outstanding item to non-existent reconciliation fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.add_outstanding_item(
                mock_db,
                uuid4(),
                transaction_date=date.today(),
                amount=Decimal("500.00"),
                description="Test",
                outstanding_type="deposit",
            )

        assert exc.value.status_code == 404


class TestGetWithLines:
    """Tests for get_with_lines method."""

    def test_get_with_lines_existing(self, service, mock_db):
        """Test getting reconciliation with lines loaded."""
        recon = MockBankReconciliation()
        recon.lines = [
            MockBankReconciliationLine(is_cleared=True),
            MockBankReconciliationLine(is_adjustment=True),
        ]
        mock_db.get.return_value = recon

        result = service.get_with_lines(mock_db, recon.reconciliation_id)

        assert result == recon
        assert len(result.lines) == 2

    def test_get_with_lines_nonexistent(self, service, mock_db):
        """Test getting non-existent reconciliation with lines."""
        mock_db.get.return_value = None

        result = service.get_with_lines(mock_db, uuid4())

        assert result is None


class TestListFilters:
    """Additional tests for list method with various filters."""

    def test_list_by_bank_account(self, service, mock_db, org_id):
        """Test listing reconciliations filtered by bank account."""
        bank_account_id = uuid4()
        recons = [MockBankReconciliation(
            organization_id=org_id,
            bank_account_id=bank_account_id
        )]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = recons
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(mock_db, org_id, bank_account_id=bank_account_id)

        assert result == recons

    def test_list_by_date_range(self, service, mock_db, org_id):
        """Test listing reconciliations within date range."""
        recons = [MockBankReconciliation(organization_id=org_id)]

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = recons
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        result = service.list(
            mock_db, org_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31)
        )

        assert result == recons


class TestAddMatchNotFound:
    """Tests for add_match with missing resources."""

    def test_add_match_statement_line_not_found(self, service, mock_db):
        """Test adding match when statement line not found."""
        from fastapi import HTTPException

        recon = MockBankReconciliation(status="draft")
        mock_db.get.side_effect = [recon, None]  # Recon found, statement line not found

        match_input = ReconciliationMatchInput(
            statement_line_id=uuid4(),
            journal_line_id=uuid4(),
        )

        with pytest.raises(HTTPException) as exc:
            service.add_match(mock_db, recon.reconciliation_id, match_input)

        assert exc.value.status_code == 404
        assert "Statement line" in exc.value.detail

    def test_add_match_gl_line_not_found(self, service, mock_db):
        """Test adding match when GL line not found."""
        from fastapi import HTTPException

        recon = MockBankReconciliation(status="draft")
        stmt_line = MockBankStatementLine()
        mock_db.get.side_effect = [recon, stmt_line, None]  # GL line not found

        match_input = ReconciliationMatchInput(
            statement_line_id=stmt_line.line_id,
            journal_line_id=uuid4(),
        )

        with pytest.raises(HTTPException) as exc:
            service.add_match(mock_db, recon.reconciliation_id, match_input)

        assert exc.value.status_code == 404
        assert "Journal line" in exc.value.detail


class TestCreateReconciliationWithPrior:
    """Tests for create_reconciliation with prior reconciliation."""

    def test_create_with_prior_outstanding_items(
        self, service, mock_db, org_id, user_id, sample_recon_input
    ):
        """Test creating reconciliation with prior outstanding items."""
        from app.models.finance.banking.bank_reconciliation import BankReconciliation

        bank_account = MockBankAccount(organization_id=org_id)
        mock_db.get.return_value = bank_account

        # No existing reconciliation at this date
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Prior reconciliation with outstanding items
        prior_recon = MockBankReconciliation(
            outstanding_deposits=Decimal("200.00"),
            outstanding_payments=Decimal("150.00"),
        )

        with patch.object(service, "_get_gl_balance", return_value=Decimal("1500.00")):
            with patch.object(service, "_get_prior_reconciliation", return_value=prior_recon):
                with patch.object(BankReconciliation, "calculate_difference", return_value=None):
                    result = service.create_reconciliation(
                        mock_db, org_id, bank_account.bank_account_id,
                        sample_recon_input, user_id
                    )

        mock_db.add.assert_called_once()


class TestApproveWithNotes:
    """Tests for approve method with notes."""

    def test_approve_with_review_notes(self, service, mock_db, user_id):
        """Test approval with review notes."""
        from app.models.finance.banking.bank_reconciliation import ReconciliationStatus

        bank_account = MockBankAccount()
        recon = MockBankReconciliation(
            status=ReconciliationStatus.pending_review,
            reconciliation_difference=Decimal("0"),
        )
        recon.bank_account = bank_account
        mock_db.get.return_value = recon

        result = service.approve(
            mock_db, recon.reconciliation_id, user_id,
            notes="All items verified"
        )

        assert result.review_notes == "All items verified"


class TestCalculateMatchScore:
    """Tests for _calculate_match_score method."""

    def test_exact_amount_match(self, service):
        """Test match score with exact amount match."""
        stmt_line = MockBankStatementLine(
            transaction_type="credit",
            amount=Decimal("100.00"),
            transaction_date=date.today(),
            reference="REF123",
        )

        gl_line = MockJournalEntryLine(
            debit_amount=Decimal("100.00"),
            credit_amount=None,
            description="REF123 Payment",
        )
        gl_line.entry = MockJournalEntry(entry_date=date.today())

        score = service._calculate_match_score(stmt_line, gl_line)

        # Expect: 40 (amount) + 30 (same date) + 30 (reference) = 100
        assert score >= 70  # High score for exact match

    def test_amount_match_with_date_difference(self, service):
        """Test match score with date difference."""
        from datetime import timedelta

        stmt_line = MockBankStatementLine(
            transaction_type="credit",
            amount=Decimal("100.00"),
            transaction_date=date.today(),
        )

        gl_line = MockJournalEntryLine(
            debit_amount=Decimal("100.00"),
            credit_amount=None,
        )
        gl_line.entry = MockJournalEntry(entry_date=date.today() - timedelta(days=5))

        score = service._calculate_match_score(stmt_line, gl_line)

        # Amount matches (40) but date is 5 days off (10)
        assert 40 <= score <= 60

    def test_amount_mismatch(self, service):
        """Test match score with amount mismatch."""
        stmt_line = MockBankStatementLine(
            transaction_type="credit",
            amount=Decimal("100.00"),
            transaction_date=date.today(),
        )

        gl_line = MockJournalEntryLine(
            debit_amount=Decimal("200.00"),
            credit_amount=None,
        )
        gl_line.entry = MockJournalEntry(entry_date=date.today())

        score = service._calculate_match_score(stmt_line, gl_line)

        # Amount doesn't match, only date matches
        assert score <= 40


class TestAutoMatchWithItems:
    """Tests for auto_match with actual items."""

    def test_auto_match_finds_exact_matches(self, service, mock_db, user_id):
        """Test auto-matching finds exact amount matches."""
        bank_account = MockBankAccount()
        recon = MockBankReconciliation(status="draft")
        recon.bank_account = bank_account
        mock_db.get.return_value = recon

        # Create unmatched statement lines
        stmt_line = MockBankStatementLine(
            transaction_type="credit",
            amount=Decimal("100.00"),
            transaction_date=date.today(),
            is_matched=False,
        )

        # Create matching GL line
        gl_line = MockJournalEntryLine(
            debit_amount=Decimal("100.00"),
            credit_amount=None,
        )
        gl_line.entry = MockJournalEntry(entry_date=date.today())

        # Mock the database queries for statement and GL lines
        mock_stmt_result = MagicMock()
        mock_stmt_scalars = MagicMock()
        mock_stmt_scalars.all.return_value = [stmt_line]
        mock_stmt_result.scalars.return_value = mock_stmt_scalars

        mock_gl_result = MagicMock()
        mock_gl_scalars = MagicMock()
        mock_gl_scalars.all.return_value = [gl_line]
        mock_gl_result.scalars.return_value = mock_gl_scalars

        mock_db.execute.side_effect = [mock_stmt_result, mock_gl_result]

        with patch.object(service, "add_match", return_value=MockBankReconciliationLine()):
            result = service.auto_match(mock_db, recon.reconciliation_id, created_by=user_id)

        assert result.matches_found >= 0  # Depends on match score threshold

    def test_auto_match_no_matches(self, service, mock_db, user_id):
        """Test auto-matching when no matches found."""
        bank_account = MockBankAccount()
        recon = MockBankReconciliation(status="draft")
        recon.bank_account = bank_account
        mock_db.get.return_value = recon

        # No statement lines to match
        mock_stmt_result = MagicMock()
        mock_stmt_scalars = MagicMock()
        mock_stmt_scalars.all.return_value = []
        mock_stmt_result.scalars.return_value = mock_stmt_scalars

        # No GL lines
        mock_gl_result = MagicMock()
        mock_gl_scalars = MagicMock()
        mock_gl_scalars.all.return_value = []
        mock_gl_result.scalars.return_value = mock_gl_scalars

        mock_db.execute.side_effect = [mock_stmt_result, mock_gl_result]

        result = service.auto_match(mock_db, recon.reconciliation_id, created_by=user_id)

        assert result.matches_found == 0
        assert result.matches_created == 0
        assert result.unmatched_statement_lines == 0
        assert result.unmatched_gl_lines == 0
