"""
Tests for GL API endpoints.

These tests mock the service layer to test API routing and serialization.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ifrs.gl import router, get_db
from tests.api.ifrs.conftest import (
    MockAccount,
    MockFiscalPeriod,
    MockJournalEntry,
    MockAccountBalance,
    MockTrialBalance,
    MockTrialBalanceLine,
    MockPostingResult,
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
def client(app, mock_db):
    """Create test client with mocked dependencies."""
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


class TestAccountsAPI:
    """Tests for GL accounts endpoints."""

    def test_create_account_success(self, client, org_id):
        """Test successful account creation."""
        mock_account = MockAccount(organization_id=org_id)
        category_id = uuid.uuid4()

        with patch("app.api.ifrs.gl.chart_of_accounts_service.create_account") as mock_create:
            mock_create.return_value = mock_account

            response = client.post(
                f"/gl/accounts?organization_id={org_id}&category_id={category_id}",
                json={
                    "account_code": "1000",
                    "account_name": "Cash",
                    "account_type": "POSTING",
                    "normal_balance": "DEBIT",
                },
            )

        assert response.status_code == 201

    def test_get_account_success(self, client, org_id):
        """Test getting an account."""
        mock_account = MockAccount(organization_id=org_id)

        with patch("app.api.ifrs.gl.chart_of_accounts_service.get") as mock_get:
            mock_get.return_value = mock_account

            response = client.get(f"/gl/accounts/{mock_account.account_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["account_code"] == "1000"

    def test_list_accounts(self, client, org_id):
        """Test listing accounts."""
        mock_accounts = [MockAccount(organization_id=org_id) for _ in range(5)]

        with patch("app.api.ifrs.gl.chart_of_accounts_service.list") as mock_list:
            mock_list.return_value = mock_accounts

            response = client.get(f"/gl/accounts?organization_id={org_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5

    def test_list_accounts_with_filters(self, client, org_id):
        """Test listing accounts with filters."""
        mock_accounts = [MockAccount(organization_id=org_id, is_active=True)]

        with patch("app.api.ifrs.gl.chart_of_accounts_service.list") as mock_list:
            mock_list.return_value = mock_accounts

            response = client.get(
                f"/gl/accounts?organization_id={org_id}&is_active=true&account_type=POSTING"
            )

        assert response.status_code == 200
        mock_list.assert_called_once()

    def test_update_account(self, client, org_id):
        """Test updating an account."""
        mock_account = MockAccount(organization_id=org_id)
        updated_account = MockAccount(
            account_id=mock_account.account_id,
            organization_id=org_id,
            account_name="Updated Cash",
        )

        with patch("app.api.ifrs.gl.chart_of_accounts_service.update_account") as mock_update:
            mock_update.return_value = updated_account

            response = client.patch(
                f"/gl/accounts/{mock_account.account_id}?organization_id={org_id}",
                json={"account_name": "Updated Cash"},
            )

        assert response.status_code == 200

    def test_deactivate_account(self, client, org_id):
        """Test deactivating an account."""
        mock_account = MockAccount(organization_id=org_id, is_active=True)
        deactivated = MockAccount(
            account_id=mock_account.account_id,
            organization_id=org_id,
            is_active=False,
        )

        with patch("app.api.ifrs.gl.chart_of_accounts_service.deactivate_account") as mock_deactivate:
            mock_deactivate.return_value = deactivated

            response = client.post(
                f"/gl/accounts/{mock_account.account_id}/deactivate?organization_id={org_id}"
            )

        assert response.status_code == 200


class TestFiscalPeriodsAPI:
    """Tests for fiscal period endpoints."""

    def test_create_fiscal_period(self, client, org_id):
        """Test creating a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id)

        with patch("app.api.ifrs.gl.fiscal_period_service.create_period") as mock_create:
            mock_create.return_value = mock_period

            response = client.post(
                f"/gl/fiscal-periods?organization_id={org_id}",
                json={
                    "period_name": "January 2024",
                    "period_type": "MONTHLY",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "fiscal_year": 2024,
                },
            )

        assert response.status_code == 201

    def test_get_fiscal_period(self, client, org_id):
        """Test getting a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id)

        with patch("app.api.ifrs.gl.fiscal_period_service.get") as mock_get:
            mock_get.return_value = mock_period

            response = client.get(f"/gl/fiscal-periods/{mock_period.fiscal_period_id}")

        assert response.status_code == 200

    def test_list_fiscal_periods(self, client, org_id):
        """Test listing fiscal periods."""
        mock_periods = [MockFiscalPeriod(organization_id=org_id, period_number=i) for i in range(12)]

        with patch("app.api.ifrs.gl.fiscal_period_service.list") as mock_list:
            mock_list.return_value = mock_periods

            response = client.get(f"/gl/fiscal-periods?organization_id={org_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 12

    def test_list_fiscal_periods_with_filters(self, client, org_id):
        """Test listing fiscal periods with filters."""
        mock_periods = [MockFiscalPeriod(organization_id=org_id, status="OPEN")]

        with patch("app.api.ifrs.gl.fiscal_period_service.list") as mock_list:
            mock_list.return_value = mock_periods

            response = client.get(
                f"/gl/fiscal-periods?organization_id={org_id}&fiscal_year=2024&status=OPEN"
            )

        assert response.status_code == 200

    def test_open_fiscal_period(self, client, org_id):
        """Test opening a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id, status="FUTURE")
        opened_period = MockFiscalPeriod(
            fiscal_period_id=mock_period.fiscal_period_id,
            organization_id=org_id,
            status="OPEN",
        )

        with patch("app.api.ifrs.gl.fiscal_period_service.open_period") as mock_open:
            mock_open.return_value = opened_period

            response = client.post(
                f"/gl/fiscal-periods/{mock_period.fiscal_period_id}/open?organization_id={org_id}"
            )

        assert response.status_code == 200

    def test_close_fiscal_period(self, client, org_id, user_id):
        """Test closing a fiscal period."""
        mock_period = MockFiscalPeriod(organization_id=org_id, status="OPEN")
        closed_period = MockFiscalPeriod(
            fiscal_period_id=mock_period.fiscal_period_id,
            organization_id=org_id,
            status="CLOSED",
        )

        with patch("app.api.ifrs.gl.fiscal_period_service.close_period") as mock_close:
            mock_close.return_value = closed_period

            response = client.post(
                f"/gl/fiscal-periods/{mock_period.fiscal_period_id}/close"
                f"?organization_id={org_id}&closed_by_user_id={user_id}"
            )

        assert response.status_code == 200


class TestJournalEntriesAPI:
    """Tests for journal entry endpoints."""

    def test_create_journal_entry(self, client, org_id, user_id):
        """Test creating a journal entry."""
        mock_entry = MockJournalEntry(organization_id=org_id)

        with patch("app.api.ifrs.gl.journal_service.create_entry") as mock_create:
            mock_create.return_value = mock_entry

            response = client.post(
                f"/gl/journal-entries?organization_id={org_id}&created_by_user_id={user_id}",
                json={
                    "fiscal_period_id": str(uuid.uuid4()),
                    "journal_date": str(date.today()),
                    "description": "Test journal entry",
                    "source_module": "GL",
                    "lines": [
                        {
                            "account_id": str(uuid.uuid4()),
                            "debit_amount": "1000.00",
                            "credit_amount": "0",
                            "currency_code": "USD",
                        },
                        {
                            "account_id": str(uuid.uuid4()),
                            "debit_amount": "0",
                            "credit_amount": "1000.00",
                            "currency_code": "USD",
                        },
                    ],
                },
            )

        assert response.status_code == 201

    def test_get_journal_entry(self, client, org_id):
        """Test getting a journal entry."""
        mock_entry = MockJournalEntry(organization_id=org_id)

        with patch("app.api.ifrs.gl.journal_service.get") as mock_get:
            mock_get.return_value = mock_entry

            response = client.get(f"/gl/journal-entries/{mock_entry.journal_entry_id}")

        assert response.status_code == 200

    def test_list_journal_entries(self, client, org_id):
        """Test listing journal entries."""
        mock_entries = [MockJournalEntry(organization_id=org_id) for _ in range(5)]

        with patch("app.api.ifrs.gl.journal_service.list") as mock_list:
            mock_list.return_value = mock_entries

            response = client.get(f"/gl/journal-entries?organization_id={org_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5

    def test_list_journal_entries_with_filters(self, client, org_id):
        """Test listing journal entries with filters."""
        mock_entries = [MockJournalEntry(organization_id=org_id, status="POSTED")]

        with patch("app.api.ifrs.gl.journal_service.list") as mock_list:
            mock_list.return_value = mock_entries

            response = client.get(
                f"/gl/journal-entries?organization_id={org_id}"
                f"&status=POSTED&start_date=2024-01-01&end_date=2024-01-31"
            )

        assert response.status_code == 200

    def test_post_journal_entry(self, client, org_id, user_id):
        """Test posting a journal entry."""
        mock_result = MockPostingResult(
            success=True,
            entry_id=uuid.uuid4(),
            entry_number="JE-0001",
            message="Posted successfully",
        )

        with patch("app.api.ifrs.gl.ledger_posting_service.post_entry") as mock_post:
            mock_post.return_value = mock_result

            response = client.post(
                f"/gl/journal-entries/{uuid.uuid4()}/post"
                f"?organization_id={org_id}&posted_by_user_id={user_id}"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_reverse_journal_entry(self, client, org_id, user_id):
        """Test reversing a journal entry."""
        mock_entry = MockJournalEntry(organization_id=org_id, status="POSTED")
        reversal_entry = MockJournalEntry(
            organization_id=org_id,
            journal_number="JE-REV-0001",
            status="POSTED",
        )

        with patch("app.api.ifrs.gl.journal_service.reverse_entry") as mock_reverse:
            mock_reverse.return_value = reversal_entry

            response = client.post(
                f"/gl/journal-entries/{mock_entry.journal_entry_id}/reverse"
                f"?reversal_date={date.today()}&organization_id={org_id}&reversed_by_user_id={user_id}"
            )

        assert response.status_code == 200


class TestAccountBalancesAPI:
    """Tests for account balance endpoints."""

    def test_get_account_balance(self, client, org_id):
        """Test getting account balance."""
        mock_account = MockAccount(organization_id=org_id)
        mock_balance = MockAccountBalance(
            account_id=mock_account.account_id,
            account_code=mock_account.account_code,
            account_name=mock_account.account_name,
            fiscal_period_id=uuid.uuid4(),
            opening_balance=Decimal("0"),
            period_debit=Decimal("1000"),
            period_credit=Decimal("500"),
            closing_balance=Decimal("500"),
            currency_code="USD",
        )

        with patch("app.services.ifrs.gl.balance_service.get_balance") as mock_get_balance, \
             patch("app.api.ifrs.gl.chart_of_accounts_service.get") as mock_get_account:
            mock_get_balance.return_value = mock_balance
            mock_get_account.return_value = mock_account

            response = client.get(
                f"/gl/balances/{mock_account.account_id}"
                f"?fiscal_period_id={mock_balance.fiscal_period_id}&organization_id={org_id}"
            )

        assert response.status_code == 200

    def test_get_account_balance_not_found(self, client, org_id):
        """Test getting non-existent account balance."""
        with patch("app.services.ifrs.gl.balance_service.get_balance") as mock_get:
            mock_get.return_value = None

            response = client.get(
                f"/gl/balances/{uuid.uuid4()}"
                f"?fiscal_period_id={uuid.uuid4()}&organization_id={org_id}"
            )

        assert response.status_code == 404

    def test_get_trial_balance(self, client, org_id):
        """Test getting trial balance."""
        fiscal_period_id = uuid.uuid4()
        mock_trial_balance = MockTrialBalance(
            fiscal_period_id=fiscal_period_id,
            period_name="January 2024",
            as_of_date=date.today(),
            lines=[],
            total_debit=Decimal("10000"),
            total_credit=Decimal("10000"),
            is_balanced=True,
        )

        with patch("app.services.ifrs.gl.balance_service.get_trial_balance") as mock_get:
            mock_get.return_value = mock_trial_balance

            response = client.get(
                f"/gl/trial-balance?organization_id={org_id}&fiscal_period_id={fiscal_period_id}"
            )

        assert response.status_code == 200
