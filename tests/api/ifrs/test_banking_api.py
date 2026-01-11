"""
Tests for Banking API endpoints.

These tests mock the service layer to test API routing and serialization.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ifrs.banking import router, get_db, _get_org_id, _get_user_id
from app.api.deps import require_tenant_auth
from tests.api.ifrs.conftest import (
    MockBankAccount,
    MockBankStatement,
    MockBankReconciliation,
    MockStatementLine,
    MockReconciliationLine,
    MockStatementImportResult,
)


@pytest.fixture
def app():
    """Create a test FastAPI app."""
    app = FastAPI()
    app.include_router(router)
    return app


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
    }


@pytest.fixture
def client(app, mock_db, mock_auth):
    """Create test client with mocked dependencies."""
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[require_tenant_auth] = lambda: mock_auth
    return TestClient(app)


class TestBankAccountAPI:
    """Tests for bank account endpoints."""

    def test_create_bank_account_success(self, client, org_id):
        """Test successful bank account creation."""
        mock_account = MockBankAccount(organization_id=org_id)

        with patch("app.api.ifrs.banking.bank_account_service.create") as mock_create:
            mock_create.return_value = mock_account

            response = client.post(
                "/banking/accounts",
                json={
                    "bank_name": "Test Bank",
                    "account_number": "1234567890",
                    "account_name": "Operating Account",
                    "gl_account_id": str(uuid.uuid4()),
                    "currency_code": "USD",
                    "account_type": "checking",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["bank_name"] == "Test Bank"

    def test_get_bank_account_success(self, client, org_id):
        """Test getting a bank account."""
        mock_account = MockBankAccount(organization_id=org_id)

        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get:
            mock_get.return_value = mock_account

            response = client.get(f"/banking/accounts/{mock_account.bank_account_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["bank_account_id"] == str(mock_account.bank_account_id)

    def test_get_bank_account_not_found(self, client):
        """Test getting non-existent bank account."""
        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get:
            mock_get.return_value = None

            response = client.get(f"/banking/accounts/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_get_bank_account_wrong_org(self, client, org_id):
        """Test getting bank account from wrong organization."""
        mock_account = MockBankAccount(organization_id=uuid.uuid4())  # Different org

        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get:
            mock_get.return_value = mock_account

            response = client.get(f"/banking/accounts/{mock_account.bank_account_id}")

        assert response.status_code == 404

    def test_list_bank_accounts(self, client, org_id):
        """Test listing bank accounts."""
        mock_accounts = [MockBankAccount(organization_id=org_id) for _ in range(3)]

        with patch("app.api.ifrs.banking.bank_account_service.list") as mock_list, \
             patch("app.api.ifrs.banking.bank_account_service.count") as mock_count:
            mock_list.return_value = mock_accounts
            mock_count.return_value = 3

            response = client.get("/banking/accounts")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["count"] == 3

    def test_list_bank_accounts_with_filters(self, client, org_id):
        """Test listing bank accounts with filters."""
        mock_accounts = [MockBankAccount(organization_id=org_id, status="active")]

        with patch("app.api.ifrs.banking.bank_account_service.list") as mock_list, \
             patch("app.api.ifrs.banking.bank_account_service.count") as mock_count:
            mock_list.return_value = mock_accounts
            mock_count.return_value = 1

            response = client.get(
                "/banking/accounts?status=active&currency_code=USD&limit=10&offset=0"
            )

        assert response.status_code == 200
        mock_list.assert_called_once()

    def test_update_bank_account(self, client, org_id):
        """Test updating a bank account."""
        mock_account = MockBankAccount(organization_id=org_id)
        updated_account = MockBankAccount(
            bank_account_id=mock_account.bank_account_id,
            organization_id=org_id,
            account_name="Updated Account",
        )

        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_account_service.update") as mock_update:
            mock_get.return_value = mock_account
            mock_update.return_value = updated_account

            response = client.put(
                f"/banking/accounts/{mock_account.bank_account_id}",
                json={
                    "account_name": "Updated Account",
                    "bank_name": "Test Bank",
                },
            )

        assert response.status_code == 200

    def test_update_bank_account_status(self, client, org_id):
        """Test updating bank account status."""
        mock_account = MockBankAccount(organization_id=org_id, status="active")
        updated_account = MockBankAccount(
            bank_account_id=mock_account.bank_account_id,
            organization_id=org_id,
            status="inactive",
        )

        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_account_service.update_status") as mock_update:
            mock_get.return_value = mock_account
            mock_update.return_value = updated_account

            response = client.patch(
                f"/banking/accounts/{mock_account.bank_account_id}/status",
                json={"status": "inactive"},
            )

        assert response.status_code == 200

    def test_get_bank_account_balance(self, client, org_id):
        """Test getting bank account GL balance."""
        mock_account = MockBankAccount(organization_id=org_id)

        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_account_service.get_gl_balance") as mock_balance:
            mock_get.return_value = mock_account
            mock_balance.return_value = Decimal("10000.00")

            response = client.get(
                f"/banking/accounts/{mock_account.bank_account_id}/balance"
            )

        assert response.status_code == 200
        data = response.json()
        assert "balance" in data


class TestBankStatementAPI:
    """Tests for bank statement endpoints."""

    def test_import_statement_success(self, client, org_id):
        """Test successful statement import."""
        mock_account = MockBankAccount(organization_id=org_id)
        mock_statement = MockBankStatement(organization_id=org_id, bank_account_id=mock_account.bank_account_id)
        mock_result = MockStatementImportResult(
            statement=mock_statement,
            lines_imported=10,
            lines_skipped=0,
        )

        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_statement_service.import_statement") as mock_import:
            mock_get.return_value = mock_account
            mock_import.return_value = mock_result

            response = client.post(
                "/banking/statements/import",
                json={
                    "bank_account_id": str(mock_account.bank_account_id),
                    "statement_number": "STMT-001",
                    "statement_date": str(date.today()),
                    "period_start": str(date.today()),
                    "period_end": str(date.today()),
                    "opening_balance": "1000.00",
                    "closing_balance": "1500.00",
                    "lines": [
                        {
                            "line_number": 1,
                            "transaction_date": str(date.today()),
                            "transaction_type": "credit",
                            "amount": "500.00",
                            "description": "Deposit",
                        }
                    ],
                },
            )

        assert response.status_code == 201

    def test_get_statement(self, client, org_id):
        """Test getting a statement."""
        mock_statement = MockBankStatement(organization_id=org_id)

        with patch("app.api.ifrs.banking.bank_statement_service.get") as mock_get:
            mock_get.return_value = mock_statement

            response = client.get(f"/banking/statements/{mock_statement.statement_id}")

        assert response.status_code == 200

    def test_get_statement_not_found(self, client):
        """Test getting non-existent statement."""
        with patch("app.api.ifrs.banking.bank_statement_service.get") as mock_get:
            mock_get.return_value = None

            response = client.get(f"/banking/statements/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_list_statements(self, client, org_id):
        """Test listing statements."""
        mock_statements = [MockBankStatement(organization_id=org_id) for _ in range(3)]

        with patch("app.api.ifrs.banking.bank_statement_service.list") as mock_list, \
             patch("app.api.ifrs.banking.bank_statement_service.count") as mock_count:
            mock_list.return_value = mock_statements
            mock_count.return_value = 3

            response = client.get("/banking/statements")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3

    def test_get_unmatched_lines(self, client, org_id):
        """Test getting unmatched statement lines."""
        mock_statement = MockBankStatement(organization_id=org_id)
        mock_lines = [
            MockStatementLine(
                statement_id=mock_statement.statement_id,
                line_number=i+1,
                is_matched=False
            )
            for i in range(5)
        ]

        with patch("app.api.ifrs.banking.bank_statement_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_statement_service.get_unmatched_lines") as mock_lines_fn:
            mock_get.return_value = mock_statement
            mock_lines_fn.return_value = mock_lines

            response = client.get(
                f"/banking/statements/{mock_statement.statement_id}/unmatched"
            )

        assert response.status_code == 200

    def test_delete_statement(self, client, org_id):
        """Test deleting a statement."""
        mock_statement = MockBankStatement(organization_id=org_id)

        with patch("app.api.ifrs.banking.bank_statement_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_statement_service.delete") as mock_delete:
            mock_get.return_value = mock_statement
            mock_delete.return_value = True

            response = client.delete(
                f"/banking/statements/{mock_statement.statement_id}"
            )

        assert response.status_code == 204


class TestBankReconciliationAPI:
    """Tests for bank reconciliation endpoints."""

    def test_create_reconciliation(self, client, org_id):
        """Test creating a reconciliation."""
        mock_account = MockBankAccount(organization_id=org_id)
        mock_recon = MockBankReconciliation(
            organization_id=org_id,
            bank_account_id=mock_account.bank_account_id,
        )

        with patch("app.api.ifrs.banking.bank_account_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_reconciliation_service.create_reconciliation") as mock_create:
            mock_get.return_value = mock_account
            mock_create.return_value = mock_recon

            response = client.post(
                "/banking/reconciliations",
                json={
                    "bank_account_id": str(mock_account.bank_account_id),
                    "reconciliation_date": str(date.today()),
                    "period_start": str(date.today()),
                    "period_end": str(date.today()),
                    "statement_opening_balance": "1000.00",
                    "statement_closing_balance": "1500.00",
                },
            )

        assert response.status_code == 201

    def test_get_reconciliation(self, client, org_id):
        """Test getting a reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id)

        with patch("app.api.ifrs.banking.bank_reconciliation_service.get") as mock_get:
            mock_get.return_value = mock_recon

            response = client.get(
                f"/banking/reconciliations/{mock_recon.reconciliation_id}"
            )

        assert response.status_code == 200

    def test_get_reconciliation_not_found(self, client):
        """Test getting non-existent reconciliation."""
        with patch("app.api.ifrs.banking.bank_reconciliation_service.get") as mock_get:
            mock_get.return_value = None

            response = client.get(f"/banking/reconciliations/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_list_reconciliations(self, client, org_id):
        """Test listing reconciliations."""
        mock_recons = [MockBankReconciliation(organization_id=org_id) for _ in range(3)]

        with patch("app.api.ifrs.banking.bank_reconciliation_service.list") as mock_list, \
             patch("app.api.ifrs.banking.bank_reconciliation_service.count") as mock_count:
            mock_list.return_value = mock_recons
            mock_count.return_value = 3

            response = client.get("/banking/reconciliations")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3

    def test_add_reconciliation_match(self, client, org_id):
        """Test adding a match to reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id)
        mock_line = MockReconciliationLine(
            reconciliation_id=mock_recon.reconciliation_id,
            match_type="manual",
        )

        with patch("app.api.ifrs.banking.bank_reconciliation_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_reconciliation_service.add_match") as mock_add:
            mock_get.return_value = mock_recon
            mock_add.return_value = mock_line

            response = client.post(
                f"/banking/reconciliations/{mock_recon.reconciliation_id}/matches",
                json={
                    "statement_line_id": str(uuid.uuid4()),
                    "journal_line_id": str(uuid.uuid4()),
                    "match_type": "manual",
                },
            )

        assert response.status_code == 201

    def test_auto_match_reconciliation(self, client, org_id):
        """Test auto-matching reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id)
        mock_result = MagicMock()
        mock_result.matches_created = 5
        mock_result.lines_unmatched = 2

        with patch("app.api.ifrs.banking.bank_reconciliation_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_reconciliation_service.auto_match") as mock_auto:
            mock_get.return_value = mock_recon
            mock_auto.return_value = mock_result

            response = client.post(
                f"/banking/reconciliations/{mock_recon.reconciliation_id}/auto-match"
            )

        assert response.status_code == 200

    def test_submit_reconciliation_for_review(self, client, org_id):
        """Test submitting reconciliation for review."""
        mock_recon = MockBankReconciliation(organization_id=org_id, status="draft")
        submitted_recon = MockBankReconciliation(
            reconciliation_id=mock_recon.reconciliation_id,
            organization_id=org_id,
            status="pending_review",
        )

        with patch("app.api.ifrs.banking.bank_reconciliation_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_reconciliation_service.submit_for_review") as mock_submit:
            mock_get.return_value = mock_recon
            mock_submit.return_value = submitted_recon

            response = client.post(
                f"/banking/reconciliations/{mock_recon.reconciliation_id}/submit"
            )

        assert response.status_code == 200

    def test_approve_reconciliation(self, client, org_id):
        """Test approving a reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id, status="pending_review")
        approved_recon = MockBankReconciliation(
            reconciliation_id=mock_recon.reconciliation_id,
            organization_id=org_id,
            status="approved",
        )

        with patch("app.api.ifrs.banking.bank_reconciliation_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_reconciliation_service.approve") as mock_approve:
            mock_get.return_value = mock_recon
            mock_approve.return_value = approved_recon

            response = client.post(
                f"/banking/reconciliations/{mock_recon.reconciliation_id}/approve"
            )

        assert response.status_code == 200

    def test_reject_reconciliation(self, client, org_id):
        """Test rejecting a reconciliation."""
        mock_recon = MockBankReconciliation(organization_id=org_id, status="pending_review")
        rejected_recon = MockBankReconciliation(
            reconciliation_id=mock_recon.reconciliation_id,
            organization_id=org_id,
            status="rejected",
        )

        with patch("app.api.ifrs.banking.bank_reconciliation_service.get") as mock_get, \
             patch("app.api.ifrs.banking.bank_reconciliation_service.reject") as mock_reject:
            mock_get.return_value = mock_recon
            mock_reject.return_value = rejected_recon

            response = client.post(
                f"/banking/reconciliations/{mock_recon.reconciliation_id}/reject",
                json={"notes": "Missing documentation"},
            )

        assert response.status_code == 200
