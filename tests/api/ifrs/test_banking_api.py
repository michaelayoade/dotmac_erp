"""
Tests for Banking API endpoints.

These tests mock the service layer to test API routing and serialization.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.finance import banking
from app.schemas.finance.banking import (
    AutoMatchRequest,
    BankAccountCreate,
    BankAccountStatusUpdate,
    BankAccountUpdate,
    BankStatementImport,
    ReconciliationApproval,
    ReconciliationCreate,
    ReconciliationMatchCreate,
    ReconciliationRejection,
    StatementLineCreate,
)
from tests.api.ifrs.conftest import (
    MockBankAccount,
    MockBankStatement,
    MockBankReconciliation,
    MockStatementLine,
    MockReconciliationLine,
    MockStatementImportResult,
)


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def mock_auth(org_id, user_id):
    """Create mock auth dictionary."""
    return {
        "person_id": str(user_id),
        "organization_id": str(org_id),
        "roles": ["admin"],
        "scopes": [],
    }


class TestBankAccountAPI:
    """Tests for bank account endpoints."""

    @pytest.mark.asyncio
    async def test_create_bank_account_success(self, mock_db, mock_auth, org_id):
        """Test successful bank account creation."""
        mock_account = MockBankAccount(organization_id=org_id)

        with (
            patch("app.api.finance.banking.bank_account_service.create") as mock_create,
            patch(
                "app.api.finance.banking._bank_account_payload_from_request",
                new=AsyncMock(),
            ) as mock_payload,
        ):
            mock_create.return_value = mock_account

            payload = BankAccountCreate(
                bank_name="Test Bank",
                account_number="1234567890",
                account_name="Operating Account",
                gl_account_id=uuid.uuid4(),
                currency_code="USD",
                account_type="checking",
            )
            mock_payload.return_value = payload
            result = await banking.create_bank_account(
                MagicMock(), auth=mock_auth, db=mock_db
            )

        assert result.bank_name == "Test Bank"

    def test_get_bank_account_success(self, mock_db, mock_auth, org_id):
        """Test getting a bank account."""
        mock_account = MockBankAccount(organization_id=org_id)

        with patch("app.api.finance.banking.bank_account_service.get") as mock_get:
            mock_get.return_value = mock_account

            result = banking.get_bank_account(
                mock_account.bank_account_id, auth=mock_auth, db=mock_db
            )

        assert str(result.bank_account_id) == str(mock_account.bank_account_id)

    def test_get_bank_account_not_found(self, mock_db, mock_auth):
        """Test getting non-existent bank account."""
        with patch("app.api.finance.banking.bank_account_service.get") as mock_get:
            mock_get.return_value = None

            with pytest.raises(HTTPException) as exc:
                banking.get_bank_account(uuid.uuid4(), auth=mock_auth, db=mock_db)

        assert exc.value.status_code == 404

    def test_get_bank_account_wrong_org(self, mock_db, mock_auth):
        """Test getting bank account from wrong organization."""
        mock_account = MockBankAccount(organization_id=uuid.uuid4())

        with patch("app.api.finance.banking.bank_account_service.get") as mock_get:
            mock_get.return_value = mock_account

            with pytest.raises(HTTPException) as exc:
                banking.get_bank_account(
                    mock_account.bank_account_id, auth=mock_auth, db=mock_db
                )

        assert exc.value.status_code == 404

    def test_list_bank_accounts(self, mock_db, mock_auth, org_id):
        """Test listing bank accounts."""
        mock_accounts = [MockBankAccount(organization_id=org_id) for _ in range(3)]

        with (
            patch("app.api.finance.banking.bank_account_service.list") as mock_list,
            patch("app.api.finance.banking.bank_account_service.count") as mock_count,
        ):
            mock_list.return_value = mock_accounts
            mock_count.return_value = 3

            result = banking.list_bank_accounts(
                None,
                None,
                None,
                50,
                0,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.count == 3
        assert len(result.items) == 3

    def test_list_bank_accounts_with_filters(self, mock_db, mock_auth, org_id):
        """Test listing bank accounts with filters."""
        mock_accounts = [MockBankAccount(organization_id=org_id, status="active")]

        with (
            patch("app.api.finance.banking.bank_account_service.list") as mock_list,
            patch("app.api.finance.banking.bank_account_service.count") as mock_count,
        ):
            mock_list.return_value = mock_accounts
            mock_count.return_value = 1

            result = banking.list_bank_accounts(
                "active",
                "USD",
                None,
                10,
                0,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.count == 1
        mock_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_bank_account(self, mock_db, mock_auth, org_id):
        """Test updating a bank account."""
        mock_account = MockBankAccount(organization_id=org_id)
        updated_account = MockBankAccount(
            bank_account_id=mock_account.bank_account_id,
            organization_id=org_id,
            account_name="Updated Account",
        )

        with (
            patch("app.api.finance.banking.bank_account_service.get") as mock_get,
            patch("app.api.finance.banking.bank_account_service.update") as mock_update,
            patch(
                "app.api.finance.banking._bank_account_payload_from_request",
                new=AsyncMock(),
            ) as mock_payload,
        ):
            mock_get.return_value = mock_account
            mock_update.return_value = updated_account

            payload = BankAccountUpdate(
                account_name="Updated Account",
                bank_name="Test Bank",
            )
            mock_payload.return_value = payload
            result = await banking.update_bank_account(
                MagicMock(),
                mock_account.bank_account_id,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.account_name == "Updated Account"

    def test_update_bank_account_status(self, mock_db, mock_auth, org_id):
        """Test updating bank account status."""
        mock_account = MockBankAccount(organization_id=org_id, status="active")
        updated_account = MockBankAccount(
            bank_account_id=mock_account.bank_account_id,
            organization_id=org_id,
            status="inactive",
        )

        with (
            patch("app.api.finance.banking.bank_account_service.get") as mock_get,
            patch(
                "app.api.finance.banking.bank_account_service.update_status"
            ) as mock_update,
        ):
            mock_get.return_value = mock_account
            mock_update.return_value = updated_account

            payload = BankAccountStatusUpdate(status="inactive")
            result = banking.update_bank_account_status(
                mock_account.bank_account_id,
                payload,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.status == "inactive"

    def test_get_bank_account_balance(self, mock_db, mock_auth, org_id):
        """Test getting bank account GL balance."""
        mock_account = MockBankAccount(organization_id=org_id)

        with (
            patch("app.api.finance.banking.bank_account_service.get") as mock_get,
            patch(
                "app.api.finance.banking.bank_account_service.get_gl_balance"
            ) as mock_balance,
        ):
            mock_get.return_value = mock_account
            mock_balance.return_value = Decimal("10000.00")

            result = banking.get_bank_account_gl_balance(
                mock_account.bank_account_id, auth=mock_auth, db=mock_db
            )

        assert "balance" in result


class TestBankStatementAPI:
    """Tests for bank statement endpoints."""

    def test_import_statement_success(self, mock_db, mock_auth, org_id):
        """Test successful statement import."""
        mock_account = MockBankAccount(organization_id=org_id)
        mock_statement = MockBankStatement(
            organization_id=org_id, bank_account_id=mock_account.bank_account_id
        )
        mock_result = MockStatementImportResult(
            statement=mock_statement,
            lines_imported=10,
            lines_skipped=0,
        )

        with (
            patch("app.api.finance.banking.bank_account_service.get") as mock_get,
            patch(
                "app.api.finance.banking.bank_statement_service.import_statement"
            ) as mock_import,
        ):
            mock_get.return_value = mock_account
            mock_import.return_value = mock_result

            payload = BankStatementImport(
                bank_account_id=mock_account.bank_account_id,
                statement_number="STMT-001",
                statement_date=date.today(),
                period_start=date.today(),
                period_end=date.today(),
                opening_balance=Decimal("1000.00"),
                closing_balance=Decimal("1500.00"),
                lines=[
                    StatementLineCreate(
                        line_number=1,
                        transaction_date=date.today(),
                        transaction_type="credit",
                        amount=Decimal("500.00"),
                        description="Deposit",
                    )
                ],
            )
            result = banking.import_bank_statement(payload, auth=mock_auth, db=mock_db)

        assert result.statement.statement_id == mock_statement.statement_id

    def test_get_statement(self, mock_db, mock_auth, org_id):
        """Test getting a statement."""
        mock_statement = MockBankStatement(organization_id=org_id)

        with patch("app.api.finance.banking.bank_statement_service.get") as mock_get:
            mock_get.return_value = mock_statement

            result = banking.get_bank_statement(
                mock_statement.statement_id, auth=mock_auth, db=mock_db
            )

        assert result.statement_id == mock_statement.statement_id

    def test_get_statement_not_found(self, mock_db, mock_auth):
        """Test getting non-existent statement."""
        with patch("app.api.finance.banking.bank_statement_service.get") as mock_get:
            mock_get.return_value = None

            with pytest.raises(HTTPException) as exc:
                banking.get_bank_statement(uuid.uuid4(), auth=mock_auth, db=mock_db)

        assert exc.value.status_code == 404

    def test_list_statements(self, mock_db, mock_auth, org_id):
        """Test listing statements."""
        mock_statements = [MockBankStatement(organization_id=org_id) for _ in range(3)]

        with (
            patch("app.api.finance.banking.bank_statement_service.list") as mock_list,
            patch("app.api.finance.banking.bank_statement_service.count") as mock_count,
        ):
            mock_list.return_value = mock_statements
            mock_count.return_value = 3

            result = banking.list_bank_statements(
                None,
                None,
                None,
                None,
                50,
                0,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.count == 3

    def test_get_unmatched_lines(self, mock_db, mock_auth, org_id):
        """Test getting unmatched statement lines."""
        mock_statement = MockBankStatement(organization_id=org_id)
        mock_lines = [
            MockStatementLine(
                statement_id=mock_statement.statement_id,
                line_number=i + 1,
                is_matched=False,
            )
            for i in range(5)
        ]

        with (
            patch("app.api.finance.banking.bank_statement_service.get") as mock_get,
            patch(
                "app.api.finance.banking.bank_statement_service.get_unmatched_lines"
            ) as mock_lines_fn,
        ):
            mock_get.return_value = mock_statement
            mock_lines_fn.return_value = mock_lines

            result = banking.get_unmatched_statement_lines(
                mock_statement.statement_id, auth=mock_auth, db=mock_db
            )

        assert len(result) == 5

    def test_delete_statement(self, mock_db, mock_auth, org_id):
        """Test deleting a statement."""
        mock_statement = MockBankStatement(organization_id=org_id)

        with (
            patch("app.api.finance.banking.bank_statement_service.get") as mock_get,
            patch(
                "app.api.finance.banking.bank_statement_service.delete"
            ) as mock_delete,
        ):
            mock_get.return_value = mock_statement
            mock_delete.return_value = True

            result = banking.delete_bank_statement(
                mock_statement.statement_id, auth=mock_auth, db=mock_db
            )

        assert result is None


class TestBankReconciliationAPI:
    """Tests for bank reconciliation endpoints."""

    def test_create_reconciliation(self, mock_db, mock_auth, org_id):
        """Test creating a reconciliation."""
        mock_account = MockBankAccount(organization_id=org_id)
        mock_recon = MockBankReconciliation(
            organization_id=org_id,
            bank_account_id=mock_account.bank_account_id,
        )

        with (
            patch("app.api.finance.banking.bank_account_service.get") as mock_get,
            patch(
                "app.api.finance.banking.bank_reconciliation_service.create_reconciliation"
            ) as mock_create,
        ):
            mock_get.return_value = mock_account
            mock_create.return_value = mock_recon

            payload = ReconciliationCreate(
                bank_account_id=mock_account.bank_account_id,
                reconciliation_date=date.today(),
                period_start=date.today(),
                period_end=date.today(),
                statement_opening_balance=Decimal("1000.00"),
                statement_closing_balance=Decimal("1500.00"),
            )
            result = banking.create_reconciliation(payload, auth=mock_auth, db=mock_db)

        assert result.reconciliation_id == mock_recon.reconciliation_id

    def test_get_reconciliation(self, mock_db, mock_auth, org_id):
        """Test getting a reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id)

        with patch(
            "app.api.finance.banking.bank_reconciliation_service.get"
        ) as mock_get:
            mock_get.return_value = mock_recon

            result = banking.get_reconciliation(
                mock_recon.reconciliation_id, auth=mock_auth, db=mock_db
            )

        assert result.reconciliation_id == mock_recon.reconciliation_id

    def test_get_reconciliation_not_found(self, mock_db, mock_auth):
        """Test getting non-existent reconciliation."""
        with patch(
            "app.api.finance.banking.bank_reconciliation_service.get"
        ) as mock_get:
            mock_get.return_value = None

            with pytest.raises(HTTPException) as exc:
                banking.get_reconciliation(uuid.uuid4(), auth=mock_auth, db=mock_db)

        assert exc.value.status_code == 404

    def test_list_reconciliations(self, mock_db, mock_auth, org_id):
        """Test listing reconciliations."""
        mock_recons = [MockBankReconciliation(organization_id=org_id) for _ in range(3)]

        with (
            patch(
                "app.api.finance.banking.bank_reconciliation_service.list"
            ) as mock_list,
            patch(
                "app.api.finance.banking.bank_reconciliation_service.count"
            ) as mock_count,
        ):
            mock_list.return_value = mock_recons
            mock_count.return_value = 3

            result = banking.list_reconciliations(
                None,
                None,
                None,
                None,
                50,
                0,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.count == 3

    def test_add_reconciliation_match(self, mock_db, mock_auth, org_id):
        """Test adding a match to reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id)
        mock_line = MockReconciliationLine(
            reconciliation_id=mock_recon.reconciliation_id,
            match_type="manual",
        )

        with (
            patch(
                "app.api.finance.banking.bank_reconciliation_service.get"
            ) as mock_get,
            patch(
                "app.api.finance.banking.bank_reconciliation_service.add_match"
            ) as mock_add,
        ):
            mock_get.return_value = mock_recon
            mock_add.return_value = mock_line

            payload = ReconciliationMatchCreate(
                statement_line_id=uuid.uuid4(),
                journal_line_id=uuid.uuid4(),
                match_type="manual",
            )
            result = banking.add_reconciliation_match(
                mock_recon.reconciliation_id,
                payload,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.match_type == "manual"

    def test_auto_match_reconciliation(self, mock_db, mock_auth, org_id):
        """Test auto-matching reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id)
        mock_result = MagicMock()
        mock_result.matches_created = 5
        mock_result.lines_unmatched = 2

        with (
            patch(
                "app.api.finance.banking.bank_reconciliation_service.get"
            ) as mock_get,
            patch(
                "app.api.finance.banking.bank_reconciliation_service.auto_match"
            ) as mock_auto,
        ):
            mock_get.return_value = mock_recon
            mock_auto.return_value = mock_result

            payload = AutoMatchRequest()
            result = banking.auto_match_reconciliation(
                mock_recon.reconciliation_id,
                payload,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.matches_created == 5

    def test_submit_reconciliation_for_review(self, mock_db, mock_auth, org_id):
        """Test submitting reconciliation for review."""
        mock_recon = MockBankReconciliation(organization_id=org_id, status="draft")
        submitted_recon = MockBankReconciliation(
            reconciliation_id=mock_recon.reconciliation_id,
            organization_id=org_id,
            status="pending_review",
        )

        with (
            patch(
                "app.api.finance.banking.bank_reconciliation_service.get"
            ) as mock_get,
            patch(
                "app.api.finance.banking.bank_reconciliation_service.submit_for_review"
            ) as mock_submit,
        ):
            mock_get.return_value = mock_recon
            mock_submit.return_value = submitted_recon

            result = banking.submit_reconciliation_for_review(
                mock_recon.reconciliation_id, auth=mock_auth, db=mock_db
            )

        assert result.status == "pending_review"

    def test_approve_reconciliation(self, mock_db, mock_auth, org_id):
        """Test approving a reconciliation."""
        mock_recon = MockBankReconciliation(
            organization_id=org_id, status="pending_review"
        )
        approved_recon = MockBankReconciliation(
            reconciliation_id=mock_recon.reconciliation_id,
            organization_id=org_id,
            status="approved",
        )

        with (
            patch(
                "app.api.finance.banking.bank_reconciliation_service.get"
            ) as mock_get,
            patch(
                "app.api.finance.banking.bank_reconciliation_service.approve"
            ) as mock_approve,
        ):
            mock_get.return_value = mock_recon
            mock_approve.return_value = approved_recon

            payload = ReconciliationApproval(review_notes=None)
            result = banking.approve_reconciliation(
                mock_recon.reconciliation_id,
                payload,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.status == "approved"

    def test_reject_reconciliation(self, mock_db, mock_auth, org_id):
        """Test rejecting a reconciliation."""
        mock_recon = MockBankReconciliation(
            organization_id=org_id, status="pending_review"
        )
        rejected_recon = MockBankReconciliation(
            reconciliation_id=mock_recon.reconciliation_id,
            organization_id=org_id,
            status="rejected",
        )

        with (
            patch(
                "app.api.finance.banking.bank_reconciliation_service.get"
            ) as mock_get,
            patch(
                "app.api.finance.banking.bank_reconciliation_service.reject"
            ) as mock_reject,
        ):
            mock_get.return_value = mock_recon
            mock_reject.return_value = rejected_recon

            payload = ReconciliationRejection(notes="Missing documentation")
            result = banking.reject_reconciliation(
                mock_recon.reconciliation_id,
                payload,
                auth=mock_auth,
                db=mock_db,
            )

        assert result.status == "rejected"
